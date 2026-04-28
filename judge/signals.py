import errno
import os
import re
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.contrib.flatpages.models import FlatPage
from django.core.cache import cache
from django.core.cache.utils import make_template_fragment_key
from django.db import transaction
from django.db.models import F, Max, Q
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone
from registration.models import RegistrationProfile
from registration.signals import user_registered

from judge.caching import finished_submission
from judge.models import BlogPost, Comment, Contest, ContestAnnouncement, ContestSubmission, EFFECTIVE_MATH_ENGINES, \
    Judge, Language, License, MiscConfig, Organization, Problem, Profile, RoadmapLevel, RoadmapLevelContest, \
    Submission, WebAuthnCredential
from judge.models.contest import ContestProblem
from judge.tasks import on_new_comment
from judge.views.register import RegistrationView


def ensure_unicorns_org_admin(org):
    admin_user = User.objects.filter(username='admin').first()
    if not admin_user:
        return
    try:
        admin_profile = admin_user.profile
    except Profile.DoesNotExist:
        return
    org.admins.add(admin_profile)
    admin_profile.organizations.add(org)
    org_admin_group, _ = Group.objects.get_or_create(name=getattr(settings, 'GROUP_PERMISSION_FOR_ORG_ADMIN', 'Org Admin'))
    admin_user.groups.add(org_admin_group)


def get_pdf_path(basename: str) -> Optional[str]:
    if not settings.DMOJ_PDF_PROBLEM_CACHE:
        return None

    return os.path.join(settings.DMOJ_PDF_PROBLEM_CACHE, basename)


def unlink_if_exists(file):
    try:
        os.unlink(file)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise


LEVEL_TYPE_PATTERN = re.compile(r'^\s*level\s*(\d+)\b', re.IGNORECASE)


def _extract_level_numbers(problem):
    levels = set()
    for value in problem.types.values_list('full_name', flat=True):
        match = LEVEL_TYPE_PATTERN.match(value or '')
        if match:
            levels.add(int(match.group(1)))
    return sorted(levels)


def _build_level_contest_key(level_number):
    base = f'level_{level_number}_luyen_tap_tong_hop'
    candidate = base[:32]
    counter = 2
    while Contest.objects.filter(key=candidate).exists():
        suffix = f'_{counter}'
        candidate = f'{base[:32 - len(suffix)]}{suffix}'
        counter += 1
    return candidate


def _pin_contest_to_top(level, contest):
    RoadmapLevelContest.objects.filter(level=level).exclude(contest=contest).update(order=F('order') + 1)
    RoadmapLevelContest.objects.update_or_create(
        level=level,
        contest=contest,
        defaults={'order': 0},
    )


@transaction.atomic
def _sync_level_contest_and_roadmap(level_number):
    now = timezone.now()
    level_name = f'Level {level_number} - Luyện tập tổng hợp'
    contest = Contest.objects.filter(name=level_name).order_by('id').first()
    if contest is None:
        contest = Contest.objects.create(
            key=_build_level_contest_key(level_number),
            name=level_name,
            start_time=now,
            end_time=now + timedelta(days=3650),
            is_visible=False,
            is_private=True,
            is_organization_private=False,
            use_clarifications=False,
            show_submission_list=True,
        )

    level_pattern = rf'^\s*level\s*{level_number}\b'
    level_problem_ids = list(
        Problem.objects.filter(
            Q(types__full_name__iregex=level_pattern) | Q(types__name__iregex=level_pattern)
        ).distinct().order_by('code').values_list('id', flat=True)
    )
    if level_problem_ids:
        problem_points = {
            row['id']: max(0, int(round(row['points'] or 0)))
            for row in Problem.objects.filter(id__in=level_problem_ids).values('id', 'points')
        }
        problem_partial = {
            row['id']: bool(row['partial'])
            for row in Problem.objects.filter(id__in=level_problem_ids).values('id', 'partial')
        }
        existing_ids = set(
            ContestProblem.objects.filter(contest=contest, problem_id__in=level_problem_ids)
            .values_list('problem_id', flat=True)
        )
        missing_ids = [problem_id for problem_id in level_problem_ids if problem_id not in existing_ids]
        if missing_ids:
            next_order = ContestProblem.objects.filter(contest=contest).aggregate(m=Max('order'))['m'] or 0
            ContestProblem.objects.bulk_create([
                ContestProblem(
                    contest=contest,
                    problem_id=problem_id,
                    points=problem_points.get(problem_id, 0),
                    partial=problem_partial.get(problem_id, True),
                    order=next_order + index + 1,
                )
                for index, problem_id in enumerate(missing_ids)
            ])

    roadmap_level, created = RoadmapLevel.objects.get_or_create(
        level=level_number,
        defaults={
            'slug': f'level-{level_number}',
            'title': f'Level {level_number}',
            'subtitle': '',
            'description': '',
            'order': level_number,
            'is_visible': True,
        },
    )
    if created:
        roadmap_level.callout_title = f'Level {level_number}'
        roadmap_level.save(update_fields=['callout_title'])

    _pin_contest_to_top(roadmap_level, contest)


@receiver(m2m_changed, sender=Problem.types.through)
def problem_level_type_sync(sender, instance, action, **kwargs):
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return

    for level_number in _extract_level_numbers(instance):
        _sync_level_contest_and_roadmap(level_number)


@receiver(post_save, sender=Problem)
def problem_update(sender, instance, **kwargs):
    if hasattr(instance, '_updating_stats_only'):
        return

    cache.delete_many([
        make_template_fragment_key('submission_problem', (instance.id,)),
        make_template_fragment_key('problem_feed', (instance.id,)),
        'problem_tls:%s' % instance.id, 'problem_mls:%s' % instance.id,
    ])
    cache.delete_many([make_template_fragment_key('problem_html', (instance.id, engine, lang))
                       for lang, _ in settings.LANGUAGES for engine in EFFECTIVE_MATH_ENGINES])
    cache.delete_many([make_template_fragment_key('problem_authors', (instance.id, lang))
                       for lang, _ in settings.LANGUAGES])
    cache.delete_many(['generated-meta-problem:%s:%d' % (lang, instance.id) for lang, _ in settings.LANGUAGES])

    for lang, _ in settings.LANGUAGES:
        cached_pdf_filename = get_pdf_path('%s.%s.pdf' % (instance.code, lang))
        if cached_pdf_filename is not None:
            unlink_if_exists(cached_pdf_filename)


@receiver(post_save, sender=Profile)
def profile_update(sender, instance, **kwargs):
    if hasattr(instance, '_updating_stats_only'):
        return

    cache.delete_many([make_template_fragment_key('user_about', (instance.id, engine))
                       for engine in EFFECTIVE_MATH_ENGINES])


@receiver(post_delete, sender=WebAuthnCredential)
def webauthn_delete(sender, instance, **kwargs):
    profile = instance.user
    if profile.webauthn_credentials.count() == 0:
        profile.is_webauthn_enabled = False
        profile.save(update_fields=['is_webauthn_enabled'])


@receiver(post_save, sender=Contest)
def contest_update(sender, instance, **kwargs):
    if hasattr(instance, '_updating_stats_only'):
        return

    cache.delete_many(['generated-meta-contest:%d' % instance.id] +
                      [make_template_fragment_key('contest_html', (instance.id, engine))
                       for engine in EFFECTIVE_MATH_ENGINES])


@receiver(post_save, sender=License)
def license_update(sender, instance, **kwargs):
    cache.delete(make_template_fragment_key('license_html', (instance.id,)))


@receiver(post_save, sender=Language)
def language_update(sender, instance, **kwargs):
    cache.delete_many([make_template_fragment_key('language_html', (instance.id,)),
                       'lang:cn_map'])


@receiver(post_save, sender=Judge)
def judge_update(sender, instance, **kwargs):
    cache.delete(make_template_fragment_key('judge_html', (instance.id,)))


@receiver(post_save, sender=Comment)
def comment_update(sender, instance, created, **kwargs):
    cache.delete('comment_feed:%d' % instance.id)
    if not created:
        return
    on_new_comment.delay(instance.id)


@receiver(post_save, sender=BlogPost)
def post_update(sender, instance, **kwargs):
    cache.delete_many([
        make_template_fragment_key('post_summary', (instance.id,)),
        'blog_slug:%d' % instance.id,
        'blog_feed:%d' % instance.id,
    ])
    cache.delete_many([make_template_fragment_key('post_content', (instance.id, engine))
                       for engine in EFFECTIVE_MATH_ENGINES])


@receiver(post_delete, sender=Submission)
def submission_delete(sender, instance, **kwargs):
    finished_submission(instance)
    instance.user._updating_stats_only = True
    instance.user.calculate_points()
    instance.problem._updating_stats_only = True
    instance.problem.update_stats()


@receiver(post_delete, sender=ContestSubmission)
def contest_submission_delete(sender, instance, **kwargs):
    participation = instance.participation
    participation.recompute_results()
    Submission.objects.filter(id=instance.submission_id).update(contest_object=None)


@receiver(post_save, sender=Organization)
def organization_update(sender, instance, **kwargs):
    cache.delete_many([make_template_fragment_key('organization_html', (instance.id, engine))
                       for engine in EFFECTIVE_MATH_ENGINES])


@receiver(m2m_changed, sender=Organization.admins.through)
def organization_admin_update(sender, instance, action, **kwargs):
    if action == 'post_add':
        pks = kwargs.get('pk_set') or set()
        for profile in Profile.objects.filter(pk__in=pks):
            profile.organizations.add(instance)


@receiver(m2m_changed, sender=User.groups.through)
def unicorns_role_group_update(sender, instance, action, **kwargs):
    if action != 'post_add':
        return
    unicorns_roles = {
        getattr(settings, 'GROUP_UNI_STUDENT', 'uni-student'),
        getattr(settings, 'GROUP_UNI_MENTOR', 'uni-mentor'),
    }
    added_group_ids = kwargs.get('pk_set') or set()
    if not added_group_ids:
        return
    added_group_names = set(instance.groups.filter(pk__in=added_group_ids).values_list('name', flat=True))
    if not (added_group_names & unicorns_roles):
        return

    org = Organization.objects.get_or_create(
        slug=getattr(settings, 'DEFAULT_UNICORNS_ORG_SLUG', 'unicorns-edu'),
        defaults={
            'name': getattr(settings, 'DEFAULT_UNICORNS_ORG_NAME', 'Unicorns Edu'),
            'short_name': 'UNICORNS',
            'about': 'Default organization for Unicorns Edu students and mentors.',
            'is_open': False,
            'is_unlisted': True,
        },
    )[0]
    ensure_unicorns_org_admin(org)
    try:
        profile = instance.profile
    except Profile.DoesNotExist:
        return
    profile.organizations.add(org)


@receiver(post_save, sender=MiscConfig)
def misc_config_update(sender, instance, **kwargs):
    cache.delete('misc_config')


@receiver(post_delete, sender=MiscConfig)
def misc_config_delete(sender, instance, **kwargs):
    cache.delete('misc_config')


@receiver(post_save, sender=ContestSubmission)
def contest_submission_update(sender, instance, **kwargs):
    Submission.objects.filter(id=instance.submission_id).update(contest_object_id=instance.participation.contest_id)


@receiver(post_save, sender=FlatPage)
def flatpage_update(sender, instance, **kwargs):
    cache.delete(make_template_fragment_key('flatpage', (instance.url, )))


@receiver(m2m_changed, sender=Profile.organizations.through)
def profile_organization_update(sender, instance, action, **kwargs):
    orgs_to_be_updated = []
    if action == 'pre_clear':
        orgs_to_be_updated = instance.organizations.get_queryset()
    if action == 'post_remove' or action == 'post_add':
        pks = kwargs.get('pk_set') or set()
        orgs_to_be_updated = Organization.objects.filter(pk__in=pks)
    for org in orgs_to_be_updated:
        org.on_user_changes()


@receiver(post_save, sender=ContestAnnouncement)
def contest_announcement_create(sender, instance, created, **kwargs):
    if not created:
        return

    instance.send()


@receiver(user_registered, sender=RegistrationView)
def registration_user_registered(sender, user, request, **kwargs):
    """Automatically activate user if SEND_ACTIVATION_EMAIL is False"""

    if not getattr(settings, 'SEND_ACTIVATION_EMAIL', True):
        # get should never fail here
        # but if it does, we won't catch it so it can show up in our log
        profile = RegistrationProfile.objects.get(user=user)

        user.is_active = True
        profile.activated = True

        with transaction.atomic():
            user.save()
            profile.save()
