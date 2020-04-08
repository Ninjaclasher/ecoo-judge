from django.core.management.base import BaseCommand

from judge.models import Problem


class Command(BaseCommand):
    help = 'create an empty problem'

    def add_arguments(self, parser):
        parser.add_argument('code', help='problem code')
        parser.add_argument('name', help='problem title')
        parser.add_argument('body', help='problem description')

    def handle(self, *args, **options):
        problem = Problem()
        problem.code = options['code']
        problem.name = options['name']
        problem.description = options['body']
        problem.save()
