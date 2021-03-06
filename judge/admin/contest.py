from django.conf.urls import url
from django.contrib import admin
from django.db import transaction
from django.db.models import Q
from django.forms import ModelForm
from django.http import HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ungettext
from reversion.admin import VersionAdmin

from django_ace import AceWidget
from judge.models import Contest, ContestProblem, ContestSubmission, Profile
from judge.utils.views import NoBatchDeleteMixin
from judge.widgets import AdminHeavySelect2MultipleWidget, AdminHeavySelect2Widget, AdminMartorWidget, \
    AdminSelect2Widget


class AdminHeavySelect2Widget(AdminHeavySelect2Widget):
    @property
    def is_hidden(self):
        return False


class ContestProblemInlineForm(ModelForm):
    class Meta:
        widgets = {'problem': AdminHeavySelect2Widget(data_view='problem_select2')}


class ContestProblemInline(admin.TabularInline):
    model = ContestProblem
    verbose_name = _('Problem')
    verbose_name_plural = 'Problems'
    fields = ('problem', 'points', 'partial', 'is_pretested', 'max_submissions', 'output_prefix_override', 'order',
              'rejudge_column')
    readonly_fields = ('rejudge_column',)
    form = ContestProblemInlineForm

    def rejudge_column(self, obj):
        if obj.id is None:
            return ''
        return format_html('<a class="button rejudge-link" href="{}">Rejudge</a>',
                           reverse('admin:judge_contest_rejudge', args=(obj.contest.id, obj.id)))
    rejudge_column.short_description = ''


class ContestForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super(ContestForm, self).__init__(*args, **kwargs)
        self.fields['banned_users'].widget.can_add_related = False

    def clean(self):
        cleaned_data = super(ContestForm, self).clean()
        cleaned_data['banned_users'].filter(current_contest__contest=self.instance).update(current_contest=None)

    class Meta:
        widgets = {
            'organizers': AdminHeavySelect2MultipleWidget(data_view='profile_select2'),
            'private_contestants': AdminHeavySelect2MultipleWidget(data_view='profile_select2',
                                                                   attrs={'style': 'width: 100%'}),
            'organizations': AdminHeavySelect2MultipleWidget(data_view='organization_select2'),
            'banned_users': AdminHeavySelect2MultipleWidget(data_view='profile_select2',
                                                            attrs={'style': 'width: 100%'}),
            'description': AdminMartorWidget(attrs={'data-markdownfy-url': reverse_lazy('contest_preview')}),
        }


class ContestAdmin(NoBatchDeleteMixin, VersionAdmin):
    fieldsets = (
        (None, {'fields': ('key', 'name', 'organizers')}),
        (_('Settings'), {'fields': ('is_visible', 'freeze_submissions', 'freeze_after', 'hide_scoreboard',
                                    'permanently_hide_scoreboard', 'run_pretests_only', 'access_code')}),
        (_('Scheduling'), {'fields': ('start_time', 'end_time', 'time_limit')}),
        (_('Details'), {'fields': ('description', 'og_image', 'logo_override_image', 'summary')}),
        (_('Format'), {'fields': ('format_name', 'format_config', 'problem_label_script')}),
        (_('Access'), {'fields': ('is_organization_private', 'is_private_viewable', 'organizations',
                                  'is_private', 'private_contestants')}),
        (_('Justice'), {'fields': ('banned_users',)}),
    )
    list_display = ('key', 'name', 'is_visible', 'start_time', 'end_time', 'time_limit', 'user_count')
    inlines = [ContestProblemInline]
    actions_on_top = True
    actions_on_bottom = True
    form = ContestForm
    date_hierarchy = 'start_time'

    def get_actions(self, request):
        actions = super(ContestAdmin, self).get_actions(request)

        for action in ('make_visible', 'make_hidden'):
            actions[action] = self.get_action(action)

        return actions

    def get_queryset(self, request):
        queryset = Contest.objects.all()
        if request.user.has_perm('judge.edit_all_contest'):
            return queryset
        else:
            return queryset.filter(organizers__id=request.profile.id)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # We need this flag because `save_related` deals with the inlines, but does not know if we have already rescored
        self._rescored = False
        to_check = ('format_config', 'format_name', 'freeze_submissions', 'freeze_after')
        if form.changed_data and any(f in form.changed_data for f in to_check):
            self._rescore(obj.key)
            self._rescored = True

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # Only rescored if we did not already do so in `save_model`
        if not self._rescored and any(formset.has_changed() for formset in formsets):
            self._rescore(form.cleaned_data['key'])

    def has_change_permission(self, request, obj=None):
        if not request.user.has_perm('judge.change_contest'):
            return False
        if request.user.has_perm('judge.edit_all_contest') or obj is None:
            return True
        return obj.organizers.filter(id=request.profile.id).exists()

    def _rescore(self, contest_key):
        from judge.tasks import rescore_contest
        transaction.on_commit(rescore_contest.s(contest_key).delay)

    def make_visible(self, request, queryset):
        count = queryset.update(is_visible=True)
        self.message_user(request, ungettext('%d contest successfully marked as visible.',
                                             '%d contests successfully marked as visible.',
                                             count) % count)
    make_visible.short_description = _('Mark contests as visible')

    def make_hidden(self, request, queryset):
        count = queryset.update(is_visible=True)
        self.message_user(request, ungettext('%d contest successfully marked as hidden.',
                                             '%d contests successfully marked as hidden.',
                                             count) % count)
    make_hidden.short_description = _('Mark contests as hidden')

    def get_urls(self):
        return [
            url(r'^(\d+)/judge/(\d+)/$', self.rejudge_view, name='judge_contest_rejudge'),
        ] + super(ContestAdmin, self).get_urls()

    def rejudge_view(self, request, contest_id, problem_id):
        queryset = ContestSubmission.objects.filter(problem_id=problem_id).select_related('submission')
        for model in queryset:
            model.submission.judge(rejudge=True)

        self.message_user(request, ungettext('%d submission was successfully scheduled for rejudging.',
                                             '%d submissions were successfully scheduled for rejudging.',
                                             len(queryset)) % len(queryset))
        return HttpResponseRedirect(reverse('admin:judge_contest_change', args=(contest_id,)))

    def get_form(self, request, obj=None, **kwargs):
        form = super(ContestAdmin, self).get_form(request, obj, **kwargs)
        if 'problem_label_script' in form.base_fields:
            # form.base_fields['problem_label_script'] does not exist when the user has only view permission
            # on the model.
            form.base_fields['problem_label_script'].widget = AceWidget('lua', request.profile.ace_theme)

        perms = ('change_contest', 'edit_all_contest')
        form.base_fields['organizers'].queryset = Profile.objects.filter(
            Q(user__is_superuser=True) |
            Q(user__groups__permissions__codename__in=perms) |
            Q(user__user_permissions__codename__in=perms),
        ).distinct()
        return form


class ContestParticipationForm(ModelForm):
    class Meta:
        widgets = {
            'contest': AdminSelect2Widget(),
            'user': AdminHeavySelect2Widget(data_view='profile_select2'),
        }


class ContestParticipationAdmin(admin.ModelAdmin):
    fields = ('contest', 'user', 'real_start', 'virtual', 'is_disqualified')
    list_display = ('contest', 'username', 'show_virtual', 'real_start', 'score', 'cumtime', 'tiebreaker')
    actions = ['recalculate_results']
    actions_on_bottom = actions_on_top = True
    search_fields = ('contest__key', 'contest__name', 'user__user__username')
    form = ContestParticipationForm
    date_hierarchy = 'real_start'

    def get_queryset(self, request):
        return super(ContestParticipationAdmin, self).get_queryset(request).only(
            'contest__name', 'contest__format_name', 'contest__format_config',
            'user__user__username', 'real_start', 'score', 'cumtime', 'tiebreaker', 'virtual',
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.changed_data and 'is_disqualified' in form.changed_data:
            obj.set_disqualified(obj.is_disqualified)

    def recalculate_results(self, request, queryset):
        count = 0
        for participation in queryset:
            participation.recompute_results()
            count += 1
        self.message_user(request, ungettext('%d participation recalculated.',
                                             '%d participations recalculated.',
                                             count) % count)
    recalculate_results.short_description = _('Recalculate results')

    def username(self, obj):
        return obj.user.username
    username.short_description = _('username')
    username.admin_order_field = 'user__user__username'

    def show_virtual(self, obj):
        return obj.virtual or '-'
    show_virtual.short_description = _('virtual')
    show_virtual.admin_order_field = 'virtual'
