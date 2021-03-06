from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from sortedm2m.fields import SortedManyToManyField

from judge.models.choices import ACE_THEMES, MATH_ENGINES_CHOICES, TIMEZONE
from judge.models.runtime import Language

__all__ = ['Organization', 'Profile']


class Organization(models.Model):
    name = models.CharField(max_length=128, verbose_name=_('organization title'))
    slug = models.SlugField(max_length=128, verbose_name=_('organization slug'),
                            help_text=_('Organization name shown in URL'))
    short_name = models.CharField(max_length=20, verbose_name=_('short name'),
                                  help_text=_('Displayed beside user name during contests'))
    about = models.TextField(verbose_name=_('organization description'))
    registrant = models.ForeignKey('Profile', verbose_name=_('registrant'), on_delete=models.CASCADE,
                                   related_name='registrant+', help_text=_('User who registered this organization'))
    admins = models.ManyToManyField('Profile', verbose_name=_('administrators'), related_name='admin_of',
                                    help_text=_('Those who can edit this organization'))
    creation_date = models.DateTimeField(verbose_name=_('creation date'), auto_now_add=True)
    slots = models.IntegerField(verbose_name=_('maximum size'), null=True, blank=True,
                                help_text=_('Maximum amount of users in this organization, '
                                            'only applicable to private organizations'))
    logo_override_image = models.CharField(verbose_name=_('Logo override image'), default='', max_length=150,
                                           blank=True,
                                           help_text=_('This image will replace the default site logo for users '
                                                       'viewing the organization.'))

    def __contains__(self, item):
        if isinstance(item, int):
            return self.members.filter(id=item).exists()
        elif isinstance(item, Profile):
            return self.members.filter(id=item.id).exists()
        else:
            raise TypeError('Organization membership test must be Profile or primany key')

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('organization_home', args=(self.id, self.slug))

    class Meta:
        ordering = ['name']
        permissions = (
            ('edit_all_organization', 'Edit all organizations'),
        )
        verbose_name = _('organization')
        verbose_name_plural = _('organizations')


class Profile(models.Model):
    user = models.OneToOneField(User, verbose_name=_('user associated'), on_delete=models.CASCADE)
    timezone = models.CharField(max_length=50, verbose_name=_('location'), choices=TIMEZONE,
                                default=settings.DEFAULT_USER_TIME_ZONE)
    language = models.ForeignKey('Language', verbose_name=_('preferred language'), on_delete=models.SET_DEFAULT,
                                 default=Language.get_default_language_pk)
    ace_theme = models.CharField(max_length=30, choices=ACE_THEMES, default='github')
    last_access = models.DateTimeField(verbose_name=_('last access time'), default=now)
    ip = models.GenericIPAddressField(verbose_name=_('last IP'), blank=True, null=True)
    organizations = SortedManyToManyField(Organization, verbose_name=_('organization'), blank=True,
                                          related_name='members', related_query_name='member')
    display_rank = models.CharField(max_length=10, default='user', verbose_name=_('display rank'),
                                    choices=(('user', 'Normal User'),
                                             ('president', 'President'),
                                             ('alumnus', 'Alumnus'),
                                             ('admin', 'Admin')))
    is_unlisted = models.BooleanField(verbose_name=_('unlisted user'), help_text=_('User will not be ranked.'),
                                      default=False)
    current_contest = models.OneToOneField('ContestParticipation', verbose_name=_('current contest'),
                                           null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    math_engine = models.CharField(verbose_name=_('math engine'), choices=MATH_ENGINES_CHOICES, max_length=4,
                                   default=settings.MATHOID_DEFAULT_TYPE,
                                   help_text=_('the rendering engine used to render math'))
    notes = models.TextField(verbose_name=_('internal notes'), null=True, blank=True,
                             help_text=_('Notes for administrators regarding this user.'))

    @cached_property
    def organization(self):
        # We do this to take advantage of prefetch_related
        orgs = self.organizations.all()
        return orgs[0] if orgs else None

    @cached_property
    def username(self):
        return self.user.username

    def remove_contest(self):
        self.current_contest = None
        self.save()

    remove_contest.alters_data = True

    def update_contest(self):
        contest = self.current_contest
        if contest is not None and (contest.ended or not contest.contest.is_accessible_by(self.user)):
            self.remove_contest()

    update_contest.alters_data = True

    def get_absolute_url(self):
        return reverse('user_page', args=(self.user.username,))

    def __str__(self):
        return self.user.username

    @classmethod
    def get_user_css_class(cls, display_rank, rating_colors=settings.DMOJ_RATING_COLORS):
        if rating_colors:
            return 'rating rate-none %s' % display_rank
        return display_rank

    @cached_property
    def css_class(self):
        return self.get_user_css_class(self.display_rank)

    class Meta:
        verbose_name = _('user profile')
        verbose_name_plural = _('user profiles')
