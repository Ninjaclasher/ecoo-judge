from datetime import datetime

from django.conf import settings
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.db.models import Max
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext as _, gettext_lazy
from django.views.generic import DetailView, ListView, TemplateView
from reversion import revisions

from judge.forms import ProfileForm
from judge.models import Profile, Submission, Ticket
from judge.utils.problems import contest_completed_ids, user_completed_ids
from judge.utils.views import DiggPaginatorMixin, TitleMixin, generic_message
from .contests import ContestRanking

__all__ = ['UserPage', 'UserAboutPage', 'UserList', 'UserDashboard', 'users', 'edit_profile']


def remap_keys(iterable, mapping):
    return [dict((mapping.get(k, k), v) for k, v in item.items()) for item in iterable]


class UserMixin(object):
    model = Profile
    slug_field = 'user__username'
    slug_url_kwarg = 'user'
    context_object_name = 'user'

    def render_to_response(self, context, **response_kwargs):
        return super(UserMixin, self).render_to_response(context, **response_kwargs)


class UserPage(TitleMixin, UserMixin, DetailView):
    template_name = 'user/user-base.html'

    def get_object(self, queryset=None):
        if self.kwargs.get(self.slug_url_kwarg, None) is None:
            return self.request.profile
        return super(UserPage, self).get_object(queryset)

    def dispatch(self, request, *args, **kwargs):
        if self.kwargs.get(self.slug_url_kwarg, None) is None:
            if not self.request.user.is_authenticated:
                return redirect_to_login(self.request.get_full_path())
        try:
            return super(UserPage, self).dispatch(request, *args, **kwargs)
        except Http404:
            return generic_message(request, _('No such user'), _('No user handle "%s".') %
                                   self.kwargs.get(self.slug_url_kwarg, None))

    def get_title(self):
        return (_('My account') if self.request.user == self.object.user else
                _('User %s') % self.object.user.username)

    # TODO: the same code exists in problem.py, maybe move to problems.py?
    @cached_property
    def profile(self):
        if not self.request.user.is_authenticated:
            return None
        return self.request.profile

    @cached_property
    def in_contest(self):
        return self.profile is not None and self.profile.current_contest is not None

    def get_completed_problems(self):
        if self.in_contest:
            return contest_completed_ids(self.profile.current_contest)
        else:
            return user_completed_ids(self.profile) if self.profile is not None else ()


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class UserDashboard(UserPage):
    template_name = 'user/user-dashboard.html'

    def get_title(self):
        return _('Dashboard')

    def get_context_data(self, **kwargs):
        if not self.request.user.is_authenticated:
            raise Http404()
        user = self.request.user
        profile = self.request.profile
        context = super(UserDashboard, self).get_context_data(**kwargs)
        context['recently_attempted_problems'] = (Submission.objects.filter(user=profile, problem__is_public=True)
                                                  .exclude(problem__id__in=user_completed_ids(profile))
                                                  .values_list('problem__code', 'problem__name', 'problem__points')
                                                  .annotate(points=Max('points'), latest=Max('date'))
                                                  .order_by('-latest')
                                                  [:settings.DMOJ_BLOG_RECENTLY_ATTEMPTED_PROBLEMS_COUNT])
        context['own_tickets'] = Ticket.tickets_list(user).filter(user=profile)[:10]

        return context


class UserAboutPage(UserPage):
    template_name = 'user/user-about.html'


@login_required
def edit_profile(request):
    if request.method == 'POST':
        form = ProfileForm(request.POST, instance=request.profile)
        if form.is_valid():
            with transaction.atomic(), revisions.create_revision():
                form.save()
                revisions.set_user(request.user)
                revisions.set_comment(_('Updated on site'))

            return HttpResponseRedirect(request.path)
    else:
        form = ProfileForm(instance=request.profile)

    return render(request, 'user/edit-profile.html', {
        'form': form, 'title': _('Edit profile'),
        'has_math_config': bool(settings.MATHOID_URL),
    })


class UserList(DiggPaginatorMixin, TitleMixin, ListView):
    model = Profile
    title = gettext_lazy('Leaderboard')
    context_object_name = 'users'
    template_name = 'user/list.html'
    paginate_by = 100

    def get_queryset(self):
        return (Profile.objects.filter(is_unlisted=False)
                .order_by('id').select_related('user')
                .only('id', 'display_rank', 'user__username'))

    def get_context_data(self, **kwargs):
        context = super(UserList, self).get_context_data(**kwargs)
        context['rank_header'] = _('Id')
        context['users'] = list(map(lambda user: (user.id, user), context['users']))
        context['first_page_href'] = '.'
        return context


user_list_view = UserList.as_view()


class FixedContestRanking(ContestRanking):
    contest = None

    def get_object(self, queryset=None):
        return self.contest


def users(request):
    if request.user.is_authenticated:
        participation = request.profile.current_contest
        if participation is not None:
            contest = participation.contest
            return FixedContestRanking.as_view(contest=contest)(request, contest=contest.key)
    return user_list_view(request)


def user_ranking_redirect(request):
    try:
        username = request.GET['handle']
    except KeyError:
        raise Http404()
    user = get_object_or_404(Profile, user__username=username)
    rank = Profile.objects.filter(is_unlisted=False, id__lt=user.id).count()
    page = rank // UserList.paginate_by
    return HttpResponseRedirect('%s%s#!%s' % (reverse('user_list'), '?page=%d' % (page + 1) if page else '', username))


class UserLogoutView(TitleMixin, TemplateView):
    template_name = 'registration/logout.html'
    title = 'You have been successfully logged out.'

    def post(self, request, *args, **kwargs):
        auth_logout(request)
        return HttpResponseRedirect(request.get_full_path())
