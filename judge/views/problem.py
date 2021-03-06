import logging
import os
import shutil
from operator import itemgetter

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.functional import cached_property
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _, gettext_lazy
from django.views.generic import DetailView, ListView, View
from django.views.generic.base import TemplateResponseMixin
from django.views.generic.detail import SingleObjectMixin

from judge.forms import ProblemCloneForm, ProblemSubmitForm
from judge.models import ContestProblem, ContestSubmission, Judge, Language, Problem, \
    ProblemTranslation, RuntimeVersion, Solution, Submission, SubmissionSource, \
    TranslatedProblemForeignKeyQuerySet
from judge.pdf_problems import DefaultPdfMaker, HAS_PDF
from judge.utils.diggpaginator import DiggPaginator
from judge.utils.opengraph import generate_opengraph
from judge.utils.problems import contest_attempted_ids, contest_completed_ids, user_attempted_ids, \
    user_completed_ids
from judge.utils.tickets import own_ticket_filter
from judge.utils.views import QueryStringSortMixin, SingleObjectFormView, TitleMixin, generic_message


def get_contest_problem(problem, profile):
    try:
        return problem.contests.get(contest_id=profile.current_contest.contest_id)
    except ObjectDoesNotExist:
        return None


def get_contest_submission_count(problem, profile, virtual):
    return profile.current_contest.submissions.exclude(submission__status__in=['IE', 'CE']) \
                  .filter(problem__problem__code=problem, participation__virtual=virtual).count()


class ProblemMixin(object):
    model = Problem
    slug_url_kwarg = 'problem'
    slug_field = 'code'

    def get_object(self, queryset=None):
        problem = super(ProblemMixin, self).get_object(queryset)
        if not problem.is_accessible_by(self.request.user):
            raise Http404()
        return problem

    def no_such_problem(self):
        code = self.kwargs.get(self.slug_url_kwarg, None)
        return generic_message(self.request, _('No such problem'),
                               _('Could not find a problem with the code "%s".') % code, status=404)

    def get(self, request, *args, **kwargs):
        try:
            return super(ProblemMixin, self).get(request, *args, **kwargs)
        except Http404:
            return self.no_such_problem()


class SolvedProblemMixin(object):
    def get_completed_problems(self):
        if self.in_contest:
            return contest_completed_ids(self.profile.current_contest)
        else:
            return user_completed_ids(self.profile) if self.profile is not None else ()

    def get_attempted_problems(self):
        if self.in_contest:
            return contest_attempted_ids(self.profile.current_contest)
        else:
            return user_attempted_ids(self.profile) if self.profile is not None else ()

    @cached_property
    def in_contest(self):
        return self.profile is not None and self.profile.current_contest is not None

    @cached_property
    def contest(self):
        return self.request.profile.current_contest.contest

    @cached_property
    def profile(self):
        if not self.request.user.is_authenticated:
            return None
        return self.request.profile


class ProblemSolution(SolvedProblemMixin, ProblemMixin, TitleMixin, DetailView):
    context_object_name = 'problem'
    template_name = 'problem/editorial.html'

    def get_title(self):
        return _('Editorial for {0}').format(self.object.name)

    def get_content_title(self):
        return format_html(_(u'Editorial for <a href="{1}">{0}</a>'), self.object.name,
                           reverse('problem_detail', args=[self.object.code]))

    def get_context_data(self, **kwargs):
        context = super(ProblemSolution, self).get_context_data(**kwargs)

        solution = get_object_or_404(Solution, problem=self.object)

        if not solution.is_public or solution.publish_on > timezone.now() or \
                (self.request.user.is_authenticated and self.request.profile.current_contest):
            raise Http404()
        context['solution'] = solution
        context['has_solved_problem'] = self.object.id in self.get_completed_problems()
        return context


class ProblemRaw(ProblemMixin, TitleMixin, TemplateResponseMixin, SingleObjectMixin, View):
    context_object_name = 'problem'
    template_name = 'problem/raw.html'

    def get_title(self):
        return self.object.name

    def get_context_data(self, **kwargs):
        context = super(ProblemRaw, self).get_context_data(**kwargs)
        context['problem_name'] = self.object.name
        context['url'] = self.request.build_absolute_uri()
        context['description'] = self.object.description
        return context

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        with translation.override(settings.LANGUAGE_CODE):
            return self.render_to_response(self.get_context_data(
                object=self.object,
            ))


class ProblemDetail(ProblemMixin, SolvedProblemMixin, DetailView):
    context_object_name = 'problem'
    template_name = 'problem/problem.html'

    def get_context_data(self, **kwargs):
        context = super(ProblemDetail, self).get_context_data(**kwargs)
        user = self.request.user
        authed = user.is_authenticated
        context['has_submissions'] = authed and Submission.objects.filter(user=user.profile,
                                                                          problem=self.object).exists()
        contest_problem = (None if not authed or user.profile.current_contest is None else
                           get_contest_problem(self.object, user.profile))
        context['contest_problem'] = contest_problem
        if contest_problem:
            clarifications = self.object.clarifications
            context['has_clarifications'] = clarifications.count() > 0
            context['clarifications'] = clarifications.order_by('-date')
            context['submission_limit'] = contest_problem.max_submissions
            if contest_problem.max_submissions:
                context['submissions_left'] = max(contest_problem.max_submissions -
                                                  get_contest_submission_count(self.object.code, user.profile,
                                                                               user.profile.current_contest.virtual), 0)

        context['available_judges'] = Judge.objects.filter(online=True, problems=self.object)
        context['show_languages'] = self.object.allowed_languages.count() != Language.objects.count()
        context['has_pdf_render'] = HAS_PDF
        context['completed_problem_ids'] = self.get_completed_problems()
        context['attempted_problems'] = self.get_attempted_problems()

        can_edit = self.object.is_editable_by(user)
        context['can_edit_problem'] = can_edit
        if user.is_authenticated:
            tickets = self.object.tickets
            if not can_edit:
                tickets = tickets.filter(own_ticket_filter(user.profile.id))
            context['has_tickets'] = tickets.exists()
            context['num_open_tickets'] = tickets.filter(is_open=True).values('id').distinct().count()

        try:
            context['editorial'] = Solution.objects.get(problem=self.object)
        except ObjectDoesNotExist:
            pass
        try:
            translation = self.object.translations.get(language=self.request.LANGUAGE_CODE)
        except ProblemTranslation.DoesNotExist:
            context['title'] = self.object.name
            context['language'] = settings.LANGUAGE_CODE
            context['description'] = self.object.description
            context['translated'] = False
        else:
            context['title'] = translation.name
            context['language'] = self.request.LANGUAGE_CODE
            context['description'] = translation.description
            context['translated'] = True

        if not self.object.og_image or not self.object.summary:
            metadata = generate_opengraph('generated-meta-problem:%s:%d' % (context['language'], self.object.id),
                                          context['description'], 'problem')
        context['meta_description'] = self.object.summary or metadata[0]
        context['og_image'] = self.object.og_image or metadata[1]
        return context


class LatexError(Exception):
    pass


class ProblemPdfView(ProblemMixin, SingleObjectMixin, View):
    logger = logging.getLogger('judge.problem.pdf')
    languages = set(map(itemgetter(0), settings.LANGUAGES))

    def get(self, request, *args, **kwargs):
        if not HAS_PDF:
            raise Http404()

        language = kwargs.get('language', self.request.LANGUAGE_CODE)
        if language not in self.languages:
            raise Http404()

        problem = self.get_object()

        try:
            trans = problem.translations.get(language=language)
        except ProblemTranslation.DoesNotExist:
            trans = None

        cache = os.path.join(settings.DMOJ_PDF_PROBLEM_CACHE, '%s.%s.pdf' % (problem.code, language))

        if not os.path.exists(cache):
            self.logger.info('Rendering: %s.%s.pdf', problem.code, language)
            with DefaultPdfMaker() as maker, translation.override(language):
                problem_name = problem.name if trans is None else trans.name
                maker.html = get_template('problem/raw.html').render({
                    'problem': problem,
                    'problem_name': problem_name,
                    'description': problem.description if trans is None else trans.description,
                    'url': request.build_absolute_uri(),
                    'math_engine': maker.math_engine,
                }).replace('"//', '"https://').replace("'//", "'https://")
                maker.title = problem_name

                assets = ['style.css', 'pygment-github.css']
                if maker.math_engine == 'jax':
                    assets.append('mathjax_config.js')
                for file in assets:
                    maker.load(file, os.path.join(settings.DMOJ_RESOURCES, file))
                maker.make()
                if not maker.success:
                    self.logger.error('Failed to render PDF for %s', problem.code)
                    return HttpResponse(maker.log, status=500, content_type='text/plain')
                shutil.move(maker.pdffile, cache)

        response = HttpResponse()
        if hasattr(settings, 'DMOJ_PDF_PROBLEM_INTERNAL') and \
                request.META.get('SERVER_SOFTWARE', '').startswith('nginx/'):
            response['X-Accel-Redirect'] = '%s/%s.%s.pdf' % (settings.DMOJ_PDF_PROBLEM_INTERNAL, problem.code, language)
        else:
            with open(cache, 'rb') as f:
                response.content = f.read()

        response['Content-Type'] = 'application/pdf'
        response['Content-Disposition'] = 'inline; filename=%s.%s.pdf' % (problem.code, language)
        return response


class ProblemList(QueryStringSortMixin, TitleMixin, SolvedProblemMixin, ListView):
    model = Problem
    title = gettext_lazy('Problems')
    context_object_name = 'problems'
    template_name = 'problem/list.html'
    paginate_by = 50
    sql_sort = frozenset(('user_count', 'code'))
    manual_sort = frozenset(('name', 'solved'))
    all_sorts = sql_sort | manual_sort
    default_sort = 'code'

    def get_paginator(self, queryset, per_page, orphans=0,
                      allow_empty_first_page=True, **kwargs):
        paginator = DiggPaginator(queryset, per_page, body=6, padding=2, orphans=orphans,
                                  allow_empty_first_page=allow_empty_first_page, **kwargs)
        if not self.in_contest:
            # Get the number of pages and then add in this magic.
            # noinspection PyStatementEffect
            paginator.num_pages

            queryset = queryset.add_i18n_name(self.request.LANGUAGE_CODE)
            sort_key = self.order.lstrip('-')
            if sort_key in self.sql_sort:
                queryset = queryset.order_by(self.order)
            elif sort_key == 'name':
                queryset = queryset.order_by(self.order.replace('name', 'i18n_name'))
            elif sort_key == 'solved':
                if self.request.user.is_authenticated:
                    profile = self.request.profile
                    solved = user_completed_ids(profile)
                    attempted = user_attempted_ids(profile)

                    def _solved_sort_order(problem):
                        if problem.id in solved:
                            return 1
                        if problem.id in attempted:
                            return 0
                        return -1

                    queryset = list(queryset)
                    queryset.sort(key=_solved_sort_order, reverse=self.order.startswith('-'))
            paginator.object_list = list(queryset)
        return paginator

    @cached_property
    def profile(self):
        if not self.request.user.is_authenticated:
            return None
        return self.request.profile

    def get_contest_queryset(self):
        queryset = self.profile.current_contest.contest.contest_problems \
            .defer('problem__description').order_by('problem__code') \
            .order_by('order')
        queryset = TranslatedProblemForeignKeyQuerySet.add_problem_i18n_name(queryset, 'i18n_name',
                                                                             self.request.LANGUAGE_CODE,
                                                                             'problem__name')
        return [{
            'id': p['problem_id'],
            'code': p['problem__code'],
            'name': p['problem__name'],
            'i18n_name': p['i18n_name'],
            'points': p['points'],
            'partial': p['partial'],
        } for p in queryset.values('problem_id', 'problem__code', 'problem__name', 'i18n_name',
                                   'points', 'partial')]

    def get_normal_queryset(self):
        return Problem.get_visible_problems(self.request.user)

    def get_queryset(self):
        if self.in_contest:
            return self.get_contest_queryset()
        else:
            return self.get_normal_queryset()

    def get_context_data(self, **kwargs):
        context = super(ProblemList, self).get_context_data(**kwargs)
        context['completed_problem_ids'] = self.get_completed_problems()
        context['attempted_problems'] = self.get_attempted_problems()

        context.update(self.get_sort_paginate_context())
        if not self.in_contest:
            context.update(self.get_sort_context())
        else:
            context['hide_contest_scoreboard'] = self.contest.hide_scoreboard
        return context

    def get(self, request, *args, **kwargs):
        # This actually copies into the instance dictionary...
        self.all_sorts = set(self.all_sorts)
        return super(ProblemList, self).get(request, *args, **kwargs)


class LanguageTemplateAjax(View):
    def get(self, request, *args, **kwargs):
        try:
            language = get_object_or_404(Language, id=int(request.GET.get('id', 0)))
        except ValueError:
            raise Http404()
        return HttpResponse(language.template, content_type='text/plain')


user_logger = logging.getLogger('judge.user')


@login_required
def problem_submit(request, problem=None, submission=None):
    if submission is not None and not request.user.has_perm('judge.resubmit_other') and \
            get_object_or_404(Submission, id=int(submission)).user.user != request.user:
        raise PermissionDenied()

    profile = request.profile
    if request.method == 'POST':
        form = ProblemSubmitForm(request.POST, instance=Submission(user=profile))
        if form.is_valid():
            limit = settings.DMOJ_SUBMISSION_LIMIT

            if (not request.user.has_perm('judge.spam_submission') and
                    Submission.objects.filter(user=profile, was_rejudged=False)
                              .exclude(status__in=['D', 'IE', 'CE', 'AB']).count() >= limit):
                return HttpResponse('<h1>You submitted too many submissions.</h1>', status=429)
            if not form.cleaned_data['problem'].allowed_languages.filter(
                    id=form.cleaned_data['language'].id).exists():
                raise PermissionDenied()
            if not form.cleaned_data['problem'].is_accessible_by(request.user):
                user_logger.info('Naughty user %s wants to submit to %s without permission',
                                 request.user.username, form.cleaned_data['problem'].code)
                return HttpResponseForbidden('<h1>Do you want me to ban you?</h1>')
            if not request.user.is_superuser and form.cleaned_data['problem'].banned_users.filter(
                    id=profile.id).exists():
                return generic_message(request, _('Banned from submitting'),
                                       _('You have been declared persona non grata for this problem. '
                                         'You are permanently barred from submitting this problem.'))

            with transaction.atomic():
                if profile.current_contest is not None:
                    contest_id = profile.current_contest.contest_id
                    try:
                        contest_problem = form.cleaned_data['problem'].contests.get(contest_id=contest_id)
                    except ContestProblem.DoesNotExist:
                        model = form.save()
                    else:
                        max_subs = contest_problem.max_submissions
                        if max_subs and get_contest_submission_count(problem, profile,
                                                                     profile.current_contest.virtual) >= max_subs:
                            return generic_message(request, _('Too many submissions'),
                                                   _('You have exceeded the submission limit for this problem.'))
                        model = form.save()
                        model.contest_object_id = contest_id

                        contest = ContestSubmission(submission=model, problem=contest_problem,
                                                    participation=profile.current_contest)
                        contest.save()
                else:
                    model = form.save()

                # Create the SubmissionSource object
                source = SubmissionSource(submission=model, source=form.cleaned_data['source'])
                source.save()
                profile.update_contest()

            # Save a query
            model.source = source
            model.judge(rejudge=False)

            return HttpResponseRedirect(reverse('submission_status', args=[str(model.id)]))
        else:
            form_data = form.cleaned_data
            if submission is not None:
                sub = get_object_or_404(Submission, id=int(submission))

            if 'problem' not in form_data:
                return HttpResponseBadRequest()
    else:
        initial = {'language': profile.language}
        if problem is not None:
            initial['problem'] = get_object_or_404(Problem, code=problem)
            problem_object = initial['problem']
            if not problem_object.is_accessible_by(request.user):
                raise Http404()
        if submission is not None:
            try:
                sub = get_object_or_404(Submission.objects.select_related('source', 'language'), id=int(submission))
                initial['source'] = sub.source.source
                initial['language'] = sub.language
            except ValueError:
                raise Http404()
        form = ProblemSubmitForm(initial=initial)
        form_data = initial
    if 'problem' in form_data:
        form.fields['language'].queryset = (
            form_data['problem'].usable_languages.order_by('name', 'key')
            .prefetch_related(Prefetch('runtimeversion_set', RuntimeVersion.objects.order_by('priority')))
        )
        problem_object = form_data['problem']
    if 'language' in form_data:
        form.fields['source'].widget.mode = form_data['language'].ace
    form.fields['source'].widget.theme = profile.ace_theme

    if submission is not None:
        default_lang = sub.language
    else:
        default_lang = request.profile.language

    submission_limit = submissions_left = None
    if profile.current_contest is not None:
        try:
            submission_limit = problem_object.contests.get(contest=profile.current_contest.contest).max_submissions
        except ContestProblem.DoesNotExist:
            pass
        else:
            if submission_limit:
                submissions_left = submission_limit - get_contest_submission_count(problem, profile,
                                                                                   profile.current_contest.virtual)
    return render(request, 'problem/submit.html', {
        'form': form,
        'title': _('Submit to %(problem)s') % {
            'problem': problem_object.translated_name(request.LANGUAGE_CODE),
        },
        'content_title': mark_safe(escape(_('Submit to %(problem)s')) % {
            'problem': format_html('<a href="{0}">{1}</a>',
                                   reverse('problem_detail', args=[problem_object.code]),
                                   problem_object.translated_name(request.LANGUAGE_CODE)),
        }),
        'langs': Language.objects.all(),
        'no_judges': not form.fields['language'].queryset,
        'submission_limit': submission_limit,
        'submissions_left': submissions_left,
        'ACE_URL': settings.ACE_URL,

        'default_lang': default_lang,
    })


class ProblemClone(ProblemMixin, TitleMixin, SingleObjectFormView):
    title = _('Clone Problem')
    template_name = 'problem/clone.html'
    form_class = ProblemCloneForm

    def get_object(self, queryset=None):
        problem = super().get_object(queryset)
        if not problem.is_editable_by(self.request.user):
            raise Http404()
        return problem

    def form_valid(self, form):
        problem = self.object

        languages = problem.allowed_languages.all()
        language_limits = problem.language_limits.all()
        problem.pk = None
        problem.is_public = False
        problem.user_count = 0
        problem.code = form.cleaned_data['code']
        problem.save()
        problem.authors.add(self.request.profile)
        problem.allowed_languages.set(languages)
        problem.language_limits.set(language_limits)

        return HttpResponseRedirect(reverse('admin:judge_problem_change', args=(problem.id,)))
