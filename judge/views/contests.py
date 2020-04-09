import json
from collections import defaultdict, namedtuple
from functools import partial
from itertools import chain
from operator import attrgetter, itemgetter

from django import forms
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Case, Count, FloatField, IntegerField, Sum, Value, When
from django.db.models.expressions import CombinedExpression
from django.http import Http404, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _, gettext_lazy
from django.views.generic import ListView
from django.views.generic.detail import BaseDetailView, DetailView, SingleObjectMixin, View

from judge import event_poster as event
from judge.forms import ContestCloneForm
from judge.models import Contest, ContestMoss, ContestParticipation, ContestProblem, \
    Problem, Submission
from judge.tasks import run_moss
from judge.utils.celery import redirect_to_task_status
from judge.utils.opengraph import generate_opengraph
from judge.utils.problems import _get_result_data
from judge.utils.ranker import ranker
from judge.utils.stats import get_bar_chart, get_pie_chart
from judge.utils.views import DiggPaginatorMixin, SingleObjectFormView, TitleMixin, generic_message, \
    paginate_query_context

__all__ = ['ContestList', 'ContestDetail', 'ContestRanking', 'ContestJoin', 'ContestLeave',
           'ContestClone', 'ContestStats', 'ContestMossView', 'ContestMossDelete', 'contest_ranking_ajax',
           'ContestParticipationDisqualify', 'get_contest_ranking_list',
           'base_contest_ranking_list']


def _find_contest(request, key, private_check=True):
    try:
        contest = Contest.objects.get(key=key)
        if private_check and not contest.is_accessible_by(request.user):
            raise ObjectDoesNotExist()
    except ObjectDoesNotExist:
        return generic_message(request, _('No such contest'),
                               _('Could not find a contest with the key "%s".') % key, status=404), False
    return contest, True


class ContestListMixin(object):
    def get_queryset(self):
        return Contest.get_visible_contests(self.request.user)


class ContestList(DiggPaginatorMixin, TitleMixin, ContestListMixin, ListView):
    model = Contest
    paginate_by = 20
    template_name = 'contest/list.html'
    title = gettext_lazy('Contests')
    context_object_name = 'past_contests'

    @cached_property
    def _now(self):
        return timezone.now()

    def _get_queryset(self):
        return super(ContestList, self).get_queryset() \
            .order_by('-start_time', 'key').prefetch_related('organizations', 'organizers')

    def get_queryset(self):
        return self._get_queryset().filter(end_time__lt=self._now)

    def get_context_data(self, **kwargs):
        context = super(ContestList, self).get_context_data(**kwargs)
        present, active, future = [], [], []
        for contest in self._get_queryset().exclude(end_time__lt=self._now):
            if contest.start_time > self._now:
                future.append(contest)
            else:
                present.append(contest)

        if self.request.user.is_authenticated:
            for participation in ContestParticipation.objects.filter(virtual=0, user=self.request.profile,
                                                                     contest_id__in=present) \
                    .select_related('contest').prefetch_related('contest__organizations', 'contest__organizers'):
                if not participation.ended:
                    active.append(participation)
                    present.remove(participation.contest)

        active.sort(key=attrgetter('end_time'))
        future.sort(key=attrgetter('start_time'))
        context['active_participations'] = active
        context['current_contests'] = present
        context['future_contests'] = future
        context['now'] = self._now
        context['first_page_href'] = '.'

        context.update(paginate_query_context(self.request))
        return context


class PrivateContestError(Exception):
    def __init__(self, name, is_private, is_organization_private, orgs):
        self.name = name
        self.is_private = is_private
        self.is_organization_private = is_organization_private
        self.orgs = orgs


class ContestMixin(object):
    context_object_name = 'contest'
    model = Contest
    slug_field = 'key'
    slug_url_kwarg = 'contest'

    @cached_property
    def is_organizer(self):
        if not self.request.user.is_authenticated:
            return False
        return self.object.organizers.filter(id=self.request.profile.id).exists()

    @cached_property
    def can_edit(self):
        return self.object.is_editable_by(self.request.user)

    def get_context_data(self, **kwargs):
        context = super(ContestMixin, self).get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            try:
                context['live_participation'] = (
                    self.request.profile.contest_history.get(
                        contest=self.object,
                        virtual=ContestParticipation.LIVE,
                    )
                )
            except ContestParticipation.DoesNotExist:
                context['live_participation'] = None
                context['has_joined'] = False
            else:
                context['has_joined'] = True
        else:
            context['live_participation'] = None
            context['has_joined'] = False

        context['now'] = timezone.now()
        context['is_organizer'] = self.is_organizer
        context['can_edit'] = self.can_edit

        if not self.object.og_image or not self.object.summary:
            metadata = generate_opengraph('generated-meta-contest:%d' % self.object.id,
                                          self.object.description, 'contest')
        context['meta_description'] = self.object.summary or metadata[0]
        context['og_image'] = self.object.og_image or metadata[1]
        context['has_moss_api_key'] = settings.MOSS_API_KEY is not None
        context['logo_override_image'] = self.object.logo_override_image
        if not context['logo_override_image'] and self.object.organizations.count() == 1:
            context['logo_override_image'] = self.object.organizations.first().logo_override_image

        return context

    def get_object(self, queryset=None):
        contest = super(ContestMixin, self).get_object(queryset)

        try:
            contest.access_check(self.request.user)
        except Contest.PrivateContest:
            raise PrivateContestError(contest.name, contest.is_private, contest.is_organization_private,
                                      contest.organizations.all())
        except Contest.Inaccessible:
            raise Http404()
        else:
            return contest

    def dispatch(self, request, *args, **kwargs):
        try:
            return super(ContestMixin, self).dispatch(request, *args, **kwargs)
        except Http404:
            key = kwargs.get(self.slug_url_kwarg, None)
            if key:
                return generic_message(request, _('No such contest'),
                                       _('Could not find a contest with the key "%s".') % key)
            else:
                return generic_message(request, _('No such contest'),
                                       _('Could not find such contest.'))
        except PrivateContestError as e:
            return render(request, 'contest/private.html', {
                'error': e, 'title': _('Access to contest "%s" denied') % e.name,
            }, status=403)


class ContestDetail(ContestMixin, TitleMixin, DetailView):
    template_name = 'contest/contest.html'

    def get_title(self):
        return self.object.name

    def get_context_data(self, **kwargs):
        context = super(ContestDetail, self).get_context_data(**kwargs)
        context['contest_problems'] = Problem.objects.filter(contests__contest=self.object) \
            .order_by('contests__order').defer('description') \
            .annotate(has_public_editorial=Sum(Case(When(solution__is_public=True, then=1),
                                                    default=0, output_field=IntegerField()))) \
            .add_i18n_name(self.request.LANGUAGE_CODE)
        return context


class ContestClone(ContestMixin, TitleMixin, SingleObjectFormView):
    title = _('Clone Contest')
    template_name = 'contest/clone.html'
    form_class = ContestCloneForm

    def get_object(self, queryset=None):
        contest = super().get_object(queryset)
        if not contest.is_editable_by(self.request.user):
            raise Http404()
        return contest

    def form_valid(self, form):
        contest = self.object

        organizations = contest.organizations.all()
        private_contestants = contest.private_contestants.all()
        contest_problems = contest.contest_problems.all()

        contest.pk = None
        contest.is_visible = False
        contest.freeze_submissions = False
        contest.user_count = 0
        contest.key = form.cleaned_data['key']
        contest.save()

        contest.organizations.set(organizations)
        contest.private_contestants.set(private_contestants)
        contest.organizers.add(self.request.profile)

        for problem in contest_problems:
            problem.contest = contest
            problem.pk = None
        ContestProblem.objects.bulk_create(contest_problems)

        return HttpResponseRedirect(reverse('admin:judge_contest_change', args=(contest.id,)))


class ContestAccessDenied(Exception):
    pass


class ContestAccessCodeForm(forms.Form):
    access_code = forms.CharField(max_length=255)

    def __init__(self, *args, **kwargs):
        super(ContestAccessCodeForm, self).__init__(*args, **kwargs)
        self.fields['access_code'].widget.attrs.update({'autocomplete': 'off'})


class ContestJoin(LoginRequiredMixin, ContestMixin, BaseDetailView):
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return self.ask_for_access_code()

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            return self.join_contest(request)
        except ContestAccessDenied:
            if request.POST.get('access_code'):
                return self.ask_for_access_code(ContestAccessCodeForm(request.POST))
            else:
                return HttpResponseRedirect(request.path)

    def join_contest(self, request, access_code=None):
        contest = self.object

        if not contest.can_join and not self.is_organizer:
            return generic_message(request, _('Contest not ongoing'),
                                   _('"%s" is not currently ongoing.') % contest.name)

        profile = request.profile
        if profile.current_contest is not None:
            return generic_message(request, _('Already in contest'),
                                   _('You are already in a contest: "%s".') % profile.current_contest.contest.name)

        if contest.ended:
            return generic_message(request, _('Cannot join contest'),
                                   _('Contest %s has ended.') % contest.name)

        if contest.is_private_viewable and \
                not contest.organizations.filter(id__in=profile.organizations.all()).exists():
            return generic_message(request, _('Cannot join contest'),
                                   _('You do not have permission to join: "%s".') % contest.name)

        if not request.user.is_superuser and contest.banned_users.filter(id=profile.id).exists():
            return generic_message(request, _('Banned from joining'),
                                   _('You have been declared persona non grata for this contest. '
                                     'You are permanently barred from joining this contest.'))

        requires_access_code = (not self.can_edit and contest.access_code and access_code != contest.access_code)
        SPECTATE = ContestParticipation.SPECTATE
        LIVE = ContestParticipation.LIVE
        try:
            participation = ContestParticipation.objects.get(
                contest=contest, user=profile, virtual=(SPECTATE if self.is_organizer else LIVE),
            )
        except ContestParticipation.DoesNotExist:
            if requires_access_code:
                raise ContestAccessDenied()

            participation = ContestParticipation.objects.create(
                contest=contest, user=profile, virtual=(SPECTATE if self.is_organizer else LIVE),
                real_start=timezone.now(),
            )
            event.post('contest_%d' % contest.id, {'type': 'update'})
        else:
            if participation.ended:
                participation = ContestParticipation.objects.get_or_create(
                    contest=contest, user=profile, virtual=SPECTATE,
                    defaults={'real_start': timezone.now()},
                )[0]

        profile.current_contest = participation
        profile.save()
        contest._updating_stats_only = True
        contest.update_user_count()

        first_problem = contest.contest_problems.order_by('order').values_list('problem__code', flat=True).first()
        if first_problem is not None:
            return HttpResponseRedirect(reverse('problem_detail', args=(first_problem,)))
        return HttpResponseRedirect(reverse('problem_list'))

    def ask_for_access_code(self, form=None):
        contest = self.object
        wrong_code = False
        if form:
            if form.is_valid():
                if form.cleaned_data['access_code'] == contest.access_code:
                    return self.join_contest(self.request, form.cleaned_data['access_code'])
                wrong_code = True
        else:
            form = ContestAccessCodeForm()
        return render(self.request, 'contest/access_code.html', {
            'form': form, 'wrong_code': wrong_code,
            'title': _('Enter access code for "%s"') % contest.name,
        })


class ContestLeave(LoginRequiredMixin, ContestMixin, BaseDetailView):
    def post(self, request, *args, **kwargs):
        contest = self.get_object()

        profile = request.profile
        if profile.current_contest is None or profile.current_contest.contest_id != contest.id:
            return generic_message(request, _('No such contest'),
                                   _('You are not in contest "%s".') % contest.key, 404)

        profile.remove_contest()
        return HttpResponseRedirect(reverse('contest_view', args=(contest.key,)))


class ContestStats(TitleMixin, ContestMixin, DetailView):
    template_name = 'contest/stats.html'

    def get_title(self):
        return _('%s Statistics') % self.object.name

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if not ((self.object.ended and self.object.can_see_full_scoreboard(self.request.user)) or
                self.can_edit):
            raise Http404()

        queryset = Submission.objects.filter(contest_object=self.object)

        ac_count = Count(Case(When(result='AC', then=Value(1)), output_field=IntegerField()))
        ac_rate = CombinedExpression(ac_count / Count('problem'), '*', Value(100.0), output_field=FloatField())

        status_count_queryset = list(
            queryset.values('problem__code', 'result').annotate(count=Count('result'))
                    .values_list('problem__code', 'result', 'count'),
        )
        labels, codes = [], []
        contest_problems = self.object.contest_problems.order_by('order').values_list('problem__name', 'problem__code')
        if contest_problems:
            labels, codes = zip(*contest_problems)
        num_problems = len(labels)
        status_counts = [[] for i in range(num_problems)]
        for problem_code, result, count in status_count_queryset:
            if problem_code in codes:
                status_counts[codes.index(problem_code)].append((result, count))

        result_data = defaultdict(partial(list, [0] * num_problems))
        for i in range(num_problems):
            for category in _get_result_data(defaultdict(int, status_counts[i]))['categories']:
                result_data[category['code']][i] = category['count']

        stats = {
            'problem_status_count': {
                'labels': labels,
                'datasets': [
                    {
                        'label': name,
                        'backgroundColor': settings.DMOJ_STATS_SUBMISSION_RESULT_COLORS[name],
                        'data': data,
                    }
                    for name, data in result_data.items()
                ],
            },
            'problem_ac_rate': get_bar_chart(
                queryset.values('contest__problem__order', 'problem__name').annotate(ac_rate=ac_rate)
                        .order_by('contest__problem__order').values_list('problem__name', 'ac_rate'),
            ),
            'language_count': get_pie_chart(
                queryset.values('language__name').annotate(count=Count('language__name'))
                        .filter(count__gt=0).order_by('-count').values_list('language__name', 'count'),
            ),
            'language_ac_rate': get_bar_chart(
                queryset.values('language__name').annotate(ac_rate=ac_rate)
                        .filter(ac_rate__gt=0).values_list('language__name', 'ac_rate'),
            ),
        }

        context['stats'] = mark_safe(json.dumps(stats))

        return context


ContestRankingProfile = namedtuple(
    'ContestRankingProfile',
    'id user css_class username points cumtime organization participation '
    'problem_cells result_cell',
)

BestSolutionData = namedtuple('BestSolutionData', 'code points bonus time state is_pretested')


def make_contest_ranking_profile(contest, participation, contest_problems):
    def display_user_problem(contest_problem):
        # When the contest format is changed, `format_data` might be invalid.
        # This will cause `display_user_problem` to error, so we display '???' instead.
        try:
            return contest.format.display_user_problem(participation, contest_problem)
        except (KeyError, TypeError, ValueError):
            return mark_safe('<td>???</td>')

    user = participation.user
    return ContestRankingProfile(
        id=user.id,
        user=user.user,
        css_class=user.css_class,
        username=user.username,
        points=participation.score,
        cumtime=participation.cumtime,
        organization=user.organization,
        problem_cells=[display_user_problem(contest_problem) for contest_problem in contest_problems],
        result_cell=contest.format.display_participation_result(participation),
        participation=participation,
    )


def base_contest_ranking_list(contest, problems, queryset):
    return [make_contest_ranking_profile(contest, participation, problems) for participation in
            queryset.select_related('user__user')]


def contest_ranking_list(contest, problems):
    return base_contest_ranking_list(contest, problems, contest.users.filter(virtual=0, user__is_unlisted=False)
                                     .order_by('is_disqualified', '-score', 'cumtime'))


def get_contest_ranking_list(request, contest, participation=None, ranking_list=contest_ranking_list,
                             show_current_virtual=True, ranker=ranker):
    problems = list(contest.contest_problems.select_related('problem').defer('problem__description').order_by('order'))

    users = ranker(ranking_list(contest, problems), key=attrgetter('points', 'cumtime'))

    if show_current_virtual:
        if participation is None and request.user.is_authenticated:
            participation = request.profile.current_contest
            if participation is None or participation.contest_id != contest.id:
                participation = None
        if participation is not None and participation.virtual:
            users = chain([('-', make_contest_ranking_profile(contest, participation, problems))], users)
    return users, problems


def contest_ranking_ajax(request, contest, participation=None):
    contest, exists = _find_contest(request, contest)
    if not exists:
        return HttpResponseBadRequest('Invalid contest', content_type='text/plain')

    if not contest.can_see_full_scoreboard(request.user):
        raise Http404()

    users, problems = get_contest_ranking_list(request, contest, participation)
    return render(request, 'contest/ranking-table.html', {
        'users': users,
        'problems': problems,
        'can_edit': contest.is_editable_by(request.user),
        'contest': contest,
    })


class ContestRankingBase(ContestMixin, TitleMixin, DetailView):
    template_name = 'contest/ranking.html'
    tab = None

    def get_title(self):
        raise NotImplementedError()

    def get_content_title(self):
        return self.object.name

    def get_ranking_list(self):
        raise NotImplementedError()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if not self.object.can_see_scoreboard(self.request.user):
            raise Http404()

        users, problems = self.get_ranking_list()
        context['users'] = users
        context['problems'] = problems
        context['last_msg'] = event.last()
        context['tab'] = self.tab
        return context


class ContestRanking(ContestRankingBase):
    tab = 'ranking'

    def get_title(self):
        return _('%s Rankings') % self.object.name

    def get_ranking_list(self):
        if not self.object.can_see_full_scoreboard(self.request.user):
            queryset = self.object.users.filter(user=self.request.profile, virtual=ContestParticipation.LIVE)
            return get_contest_ranking_list(
                self.request, self.object,
                ranking_list=partial(base_contest_ranking_list, queryset=queryset),
                ranker=lambda users, key: ((_('???'), user) for user in users),
            )

        return get_contest_ranking_list(self.request, self.object)


class ContestParticipationDisqualify(ContestMixin, SingleObjectMixin, View):
    def get_object(self, queryset=None):
        contest = super().get_object(queryset)
        if not contest.is_editable_by(self.request.user):
            raise Http404()
        return contest

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()

        try:
            participation = self.object.users.get(pk=request.POST.get('participation'))
        except ObjectDoesNotExist:
            pass
        else:
            participation.set_disqualified(not participation.is_disqualified)
        return HttpResponseRedirect(reverse('contest_ranking', args=(self.object.key,)))


class ContestMossMixin(ContestMixin):
    def get_object(self, queryset=None):
        contest = super().get_object(queryset)
        if settings.MOSS_API_KEY is None or not contest.is_editable_by(self.request.user):
            raise Http404()
        return contest


class ContestMossView(ContestMossMixin, TitleMixin, DetailView):
    template_name = 'contest/moss.html'

    def get_title(self):
        return _('%s MOSS Results') % self.object.name

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        problems = list(map(attrgetter('problem'), self.object.contest_problems.order_by('order')
                                                              .select_related('problem')))
        languages = list(map(itemgetter(0), ContestMoss.LANG_MAPPING))

        results = ContestMoss.objects.filter(contest=self.object)
        moss_results = defaultdict(list)
        for result in results:
            moss_results[result.problem].append(result)

        for result_list in moss_results.values():
            result_list.sort(key=lambda x: languages.index(x.language))

        context['languages'] = languages
        context['has_results'] = results.exists()
        context['moss_results'] = [(problem, moss_results[problem]) for problem in problems]

        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        status = run_moss.delay(self.object.key)
        return redirect_to_task_status(
            status, message=_('Running MOSS for %s...') % (self.object.name,),
            redirect=reverse('contest_moss', args=(self.object.key,)),
        )


class ContestMossDelete(ContestMossMixin, SingleObjectMixin, View):
    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        ContestMoss.objects.filter(contest=self.object).delete()
        return HttpResponseRedirect(reverse('contest_moss', args=(self.object.key,)))
