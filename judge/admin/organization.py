from django.contrib import admin
from django.forms import ModelForm
from django.urls import reverse_lazy
from django.utils.html import format_html
from django.utils.translation import gettext, gettext_lazy as _
from reversion.admin import VersionAdmin

from judge.models import Organization
from judge.widgets import AdminHeavySelect2MultipleWidget, AdminHeavySelect2Widget, AdminMartorWidget


class OrganizationForm(ModelForm):
    class Meta:
        widgets = {
            'admins': AdminHeavySelect2MultipleWidget(data_view='profile_select2', attrs={'style': 'width: 100%'}),
            'registrant': AdminHeavySelect2Widget(data_view='profile_select2', attrs={'style': 'width: 100%'}),
            'about': AdminMartorWidget(attrs={'data-markdownfy-url': reverse_lazy('organization_preview')}),
        }


class OrganizationAdmin(VersionAdmin):
    readonly_fields = ('creation_date',)
    fields = ('name', 'slug', 'short_name', 'about', 'logo_override_image', 'slots', 'registrant',
              'creation_date', 'admins')
    list_display = ('name', 'short_name', 'slots', 'registrant', 'show_public')
    prepopulated_fields = {'slug': ('name',)}
    actions_on_top = True
    actions_on_bottom = True
    form = OrganizationForm

    def show_public(self, obj):
        return format_html('<a href="{0}" style="white-space:nowrap;">{1}</a>',
                           obj.get_absolute_url(), gettext('View on site'))

    show_public.short_description = ''

    def get_queryset(self, request):
        queryset = Organization.objects.all()
        if request.user.has_perm('judge.edit_all_organization'):
            return queryset
        else:
            return queryset.filter(admins=request.profile.id)

    def has_change_permission(self, request, obj=None):
        if not request.user.has_perm('judge.change_organization'):
            return False
        if request.user.has_perm('judge.edit_all_organization') or obj is None:
            return True
        return obj.admins.filter(id=request.profile.id).exists()
