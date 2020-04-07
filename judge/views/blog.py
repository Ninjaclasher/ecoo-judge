from django.conf import settings
from django.db.models import Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import lazy
from django.utils.translation import ugettext as _
from django.views.generic import ListView

from judge.models import BlogPost, Contest, Language, Problem, ProblemClarification, Profile, Submission, \
    Ticket
from judge.utils.diggpaginator import DiggPaginator
from judge.utils.views import TitleMixin


class PostList(ListView):
    model = BlogPost
    paginate_by = 10
    context_object_name = 'posts'
    template_name = 'blog/list.html'
    title = None

    def get_paginator(self, queryset, per_page, orphans=0,
                      allow_empty_first_page=True, **kwargs):
        return DiggPaginator(queryset, per_page, body=6, padding=2,
                             orphans=orphans, allow_empty_first_page=allow_empty_first_page, **kwargs)

    def get_queryset(self):
        queryset = BlogPost.objects.filter(visible=True, publish_on__lte=timezone.now()) \
                                   .order_by('-sticky', '-publish_on') \
                                   .prefetch_related('authors__user', 'organizations')
        if not self.request.user.has_perm('judge.edit_all_post'):
            filter = Q(is_organization_private=False)
            if self.request.user.is_authenticated:
                filter |= Q(organizations__in=self.request.profile.organizations.all())
            queryset = queryset.filter(filter)
        return queryset

    def get_context_data(self, **kwargs):
        context = super(PostList, self).get_context_data(**kwargs)
        context['title'] = self.title or _('Page %d of Posts') % context['page_obj'].number
        context['first_page_href'] = reverse('home')
        context['page_prefix'] = reverse('blog_post_list')
        context['new_problems'] = Problem.objects.filter(is_public=True, is_organization_private=False) \
                                         .order_by('-date', '-id')[:settings.DMOJ_BLOG_NEW_PROBLEM_COUNT]

        context['has_clarifications'] = False
        if self.request.user.is_authenticated:
            participation = self.request.profile.current_contest
            if participation:
                clarifications = ProblemClarification.objects.filter(problem__in=participation.contest.problems.all())
                context['has_clarifications'] = clarifications.count() > 0
                context['clarifications'] = clarifications.order_by('-date')

        context['user_count'] = lazy(Profile.objects.count, int, int)
        context['problem_count'] = lazy(Problem.objects.filter(is_public=True).count, int, int)
        context['submission_count'] = lazy(Submission.objects.count, int, int)
        context['language_count'] = lazy(Language.objects.count, int, int)

        now = timezone.now()

        visible_contests = Contest.get_visible_contests(self.request.user).order_by('start_time')

        context['current_contests'] = visible_contests.filter(start_time__lte=now, end_time__gt=now)
        context['future_contests'] = visible_contests.filter(start_time__gt=now)

        if self.request.user.is_authenticated:
            context['own_open_tickets'] = Ticket.tickets_list(self.request.user).filter(user=self.request.profile)
        else:
            context['own_open_tickets'] = []

        # Superusers better be staffs, not the spell-casting kind either.
        if self.request.user.is_staff:
            context['open_tickets'] = Ticket.tickets_list(self.request.user)[:10]
        else:
            context['open_tickets'] = []
        return context


class PostView(TitleMixin):
    model = BlogPost
    pk_url_kwarg = 'id'
    context_object_name = 'post'
    template_name = 'blog/blog.html'

    def get_title(self):
        return self.object.title

    def get_context_data(self, **kwargs):
        context = super(PostView, self).get_context_data(**kwargs)
        context['og_image'] = self.object.og_image
        return context

    def get_object(self, queryset=None):
        post = super(PostView, self).get_object(queryset)
        if not post.can_see(self.request.user):
            raise Http404()
        return post
