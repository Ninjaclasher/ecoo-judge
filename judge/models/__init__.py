from reversion import revisions

from judge.models.choices import ACE_THEMES, EFFECTIVE_MATH_ENGINES, MATH_ENGINES_CHOICES, TIMEZONE
from judge.models.contest import Contest, ContestMoss, ContestParticipation, ContestProblem, \
    ContestRegistration, ContestSubmission, ContestTag
from judge.models.interface import BlogPost, MiscConfig, NavigationBar, validate_regex
from judge.models.message import PrivateMessage, PrivateMessageThread
from judge.models.problem import LanguageLimit, License, Problem, ProblemClarification, \
    ProblemTranslation, Solution, TranslatedProblemForeignKeyQuerySet, TranslatedProblemQuerySet
from judge.models.problem_data import CHECKERS, ProblemData, ProblemTestCase, problem_data_storage, \
    problem_directory_file
from judge.models.profile import Organization, OrganizationRequest, Profile
from judge.models.runtime import Judge, Language, RuntimeVersion
from judge.models.submission import SUBMISSION_RESULT, Submission, SubmissionSource, SubmissionTestCase
from judge.models.ticket import Ticket, TicketMessage

revisions.register(Profile, exclude=['points', 'last_access', 'ip'])
revisions.register(Problem, follow=['language_limits'])
revisions.register(LanguageLimit)
revisions.register(Contest, follow=['contest_problems'])
revisions.register(ContestProblem)
revisions.register(Organization)
revisions.register(BlogPost)
revisions.register(Solution)
revisions.register(Judge, fields=['name', 'created', 'auth_key', 'description'])
revisions.register(Language)
del revisions
