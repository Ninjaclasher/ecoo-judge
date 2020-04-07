from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.flatpages.models import FlatPage

from judge.admin.contest import ContestAdmin, ContestParticipationAdmin, ContestRegistrationAdmin, ContestTagAdmin
from judge.admin.interface import BlogPostAdmin, FlatPageAdmin, LicenseAdmin, LogEntryAdmin, NavigationBarAdmin
from judge.admin.organization import OrganizationAdmin, OrganizationRequestAdmin
from judge.admin.problem import ProblemAdmin
from judge.admin.profile import ProfileAdmin
from judge.admin.runtime import JudgeAdmin, LanguageAdmin
from judge.admin.submission import SubmissionAdmin
from judge.admin.taxon import ProblemGroupAdmin
from judge.admin.ticket import TicketAdmin
from judge.models import BlogPost, Contest, ContestParticipation, \
    ContestRegistration, ContestTag, Judge, Language, License, MiscConfig, NavigationBar, \
    Organization, OrganizationRequest, Problem, ProblemGroup, Profile, Submission, Ticket

admin.site.register(BlogPost, BlogPostAdmin)
admin.site.register(Contest, ContestAdmin)
admin.site.register(ContestParticipation, ContestParticipationAdmin)
admin.site.register(ContestRegistration, ContestRegistrationAdmin)
admin.site.register(ContestTag, ContestTagAdmin)
admin.site.unregister(FlatPage)
admin.site.register(FlatPage, FlatPageAdmin)
admin.site.register(Judge, JudgeAdmin)
admin.site.register(Language, LanguageAdmin)
admin.site.register(License, LicenseAdmin)
admin.site.register(LogEntry, LogEntryAdmin)
admin.site.register(MiscConfig)
admin.site.register(NavigationBar, NavigationBarAdmin)
admin.site.register(Organization, OrganizationAdmin)
admin.site.register(OrganizationRequest, OrganizationRequestAdmin)
admin.site.register(Problem, ProblemAdmin)
admin.site.register(ProblemGroup, ProblemGroupAdmin)
admin.site.register(Profile, ProfileAdmin)
admin.site.register(Submission, SubmissionAdmin)
admin.site.register(Ticket, TicketAdmin)
