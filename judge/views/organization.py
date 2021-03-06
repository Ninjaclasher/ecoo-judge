from django.db.models import Count
from django.http import Http404, HttpResponsePermanentRedirect
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy
from django.views.generic import DetailView, ListView

from judge.models import BlogPost, Organization
from judge.utils.views import TitleMixin, generic_message

__all__ = ['OrganizationList', 'OrganizationHome']


class OrganizationMixin(object):
    context_object_name = 'organization'
    model = Organization

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['logo_override_image'] = self.object.logo_override_image
        return context

    def dispatch(self, request, *args, **kwargs):
        try:
            return super(OrganizationMixin, self).dispatch(request, *args, **kwargs)
        except Http404:
            key = kwargs.get(self.slug_url_kwarg, None)
            if key:
                return generic_message(request, _('No such organization'),
                                       _('Could not find an organization with the key "%s".') % key)
            else:
                return generic_message(request, _('No such organization'),
                                       _('Could not find such organization.'))

    def can_edit_organization(self, org=None):
        if org is None:
            org = self.object
        if not self.request.user.is_authenticated:
            return False
        if self.request.user.has_perm('judge.edit_all_organization'):
            return True
        profile_id = self.request.profile.id
        return org.admins.filter(id=profile_id).exists() or org.registrant_id == profile_id


class OrganizationDetailView(OrganizationMixin, DetailView):
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.slug != kwargs['slug']:
            return HttpResponsePermanentRedirect(request.get_full_path().replace(kwargs['slug'], self.object.slug))
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)


class OrganizationList(TitleMixin, ListView):
    model = Organization
    context_object_name = 'organizations'
    template_name = 'organization/list.html'
    title = gettext_lazy('Organizations')

    def get_queryset(self):
        return super(OrganizationList, self).get_queryset().annotate(member_count=Count('member'))


class OrganizationHome(OrganizationDetailView):
    template_name = 'organization/home.html'

    def get_context_data(self, **kwargs):
        context = super(OrganizationHome, self).get_context_data(**kwargs)
        context['title'] = self.object.name
        context['can_edit'] = self.can_edit_organization()
        context['is_member'] = self.request.profile in self.object if self.request.user.is_authenticated else False
        context['posts'] = BlogPost.objects.filter(visible=True, publish_on__lte=timezone.now(),
                                                   is_organization_private=True, organizations=self.object) \
                                           .order_by('-sticky', '-publish_on') \
                                           .prefetch_related('authors__user', 'organizations')
        return context
