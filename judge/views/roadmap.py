from collections import defaultdict

from django.contrib import messages
from django.db.models import Max
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from judge.models import Contest, ContestProblem, RoadmapLevel, RoadmapLevelContest, Submission
from judge.views import TitledTemplateView


def can_edit_roadmap(user):
    return user.is_authenticated and (
        user.has_perm('judge.roadmap_level_edit_mode') or
        user.is_staff or
        user.has_perm('judge.edit_all_contest')
    )


def can_view_roadmap(user):
    return user.is_authenticated and (
        user.has_perm('judge.view_roadmap') or
        can_edit_roadmap(user)
    )


def visible_roadmap_contests(level, user):
    links = level.roadmap_contests.select_related('contest').order_by('order', 'id')
    return [link for link in links if link.contest.is_accessible_by(user)]


def level_progress(user, contest_ids):
    if not contest_ids:
        return 0, 0

    problem_ids = set(ContestProblem.objects.filter(contest_id__in=contest_ids)
                      .values_list('problem_id', flat=True).distinct())
    total = len(problem_ids)
    if not user.is_authenticated or not total:
        return 0, total

    solved = Submission.objects.filter(user=user.profile, result='AC', problem_id__in=problem_ids) \
        .values('problem_id').distinct().count()
    return solved, total


def per_contest_progress(user, contest_links):
    """Return mapping contest_id -> (solved, total) for visible contest links."""
    if not contest_links:
        return {}

    contest_ids = [link.contest_id for link in contest_links]
    contest_to_problems = defaultdict(set)
    for contest_id, problem_id in ContestProblem.objects.filter(contest_id__in=contest_ids) \
            .values_list('contest_id', 'problem_id'):
        contest_to_problems[contest_id].add(problem_id)

    all_pids = set()
    for pids in contest_to_problems.values():
        all_pids |= pids

    solved_set = set()
    if user.is_authenticated and all_pids:
        solved_set = set(
            Submission.objects.filter(
                user=user.profile, result='AC', problem_id__in=all_pids,
            ).values_list('problem_id', flat=True).distinct(),
        )

    out = {}
    for cid in contest_ids:
        pids = contest_to_problems[cid]
        total = len(pids)
        solved = len(pids & solved_set) if total else 0
        out[cid] = (solved, total)
    return out


class RoadmapOverview(TitledTemplateView):
    title = _('Roadmap')
    template_name = 'roadmap/list.html'

    def dispatch(self, request, *args, **kwargs):
        if not can_view_roadmap(request.user):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        levels = list(RoadmapLevel.objects.filter(is_visible=True).order_by('order', 'level')
                      .prefetch_related('roadmap_contests__contest'))

        level_cards = []
        for level in levels:
            links = visible_roadmap_contests(level, self.request.user)
            contest_ids = [link.contest_id for link in links]
            solved, total = level_progress(self.request.user, contest_ids)
            level_cards.append({
                'level': level,
                'contest_count': len(links),
                'solved': solved,
                'total': total,
                'progress_percent': int((solved * 100 / total) if total else 0),
            })

        context['level_cards'] = level_cards
        context['can_edit_roadmap'] = can_edit_roadmap(self.request.user)
        return context


class RoadmapLevelDetail(TemplateView):
    template_name = 'roadmap/detail.html'

    def dispatch(self, request, *args, **kwargs):
        if not can_view_roadmap(request.user):
            raise PermissionDenied()
        self.level = self.get_level()
        return super().dispatch(request, *args, **kwargs)

    def get_level(self):
        slug = self.kwargs.get('slug')
        queryset = RoadmapLevel.objects.all()
        if not can_edit_roadmap(self.request.user):
            queryset = queryset.filter(is_visible=True)
        try:
            return queryset.get(slug=slug)
        except RoadmapLevel.DoesNotExist:
            raise Http404()

    def post(self, request, *args, **kwargs):
        if not can_edit_roadmap(request.user):
            return HttpResponseForbidden()

        keep_edit_mode = request.POST.get('keep_edit_mode') == '1'
        action = request.POST.get('action')
        if action == 'add':
            contest_id = request.POST.get('contest_id')
            if contest_id:
                contest = Contest.objects.filter(id=contest_id).first()
                if contest:
                    max_order = self.level.roadmap_contests.aggregate(m=Max('order'))['m'] or 0
                    RoadmapLevelContest.objects.get_or_create(
                        level=self.level,
                        contest=contest,
                        defaults={'order': max_order + 1},
                    )
                    messages.success(request, _('Contest has been added to this level.'))
        elif action == 'reorder':
            ordered_ids = request.POST.get('ordered_ids', '')
            ids = [int(value) for value in ordered_ids.split(',') if value.isdigit()]
            lookup = {row.id: row for row in self.level.roadmap_contests.all()}
            for index, row_id in enumerate(ids):
                row = lookup.get(row_id)
                if row and row.order != index:
                    row.order = index
                    row.save(update_fields=['order'])
            messages.success(request, _('Roadmap order has been updated.'))
        elif action == 'remove':
            mapping_id = request.POST.get('mapping_id')
            if mapping_id and mapping_id.isdigit():
                RoadmapLevelContest.objects.filter(level=self.level, id=int(mapping_id)).delete()
                messages.success(request, _('Contest has been removed from this level.'))
        target = reverse('roadmap_level_detail', kwargs={'slug': self.level.slug})
        if keep_edit_mode:
            target = f'{target}?edit=1'
        return redirect(target)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contest_links = visible_roadmap_contests(self.level, self.request.user)
        contest_ids = [link.contest_id for link in contest_links]
        solved, total = level_progress(self.request.user, contest_ids)
        progress_by_contest = per_contest_progress(self.request.user, contest_links)
        edit_roadmap = can_edit_roadmap(self.request.user)

        entries = []
        for index, link in enumerate(contest_links, start=1):
            solved_c, total_c = progress_by_contest.get(link.contest_id, (0, 0))
            problem_count = total_c
            progress_percent = int((solved_c * 100 / problem_count) if problem_count else 0)
            entries.append({
                'mapping_id': link.id,
                'order': link.order,
                'contest': link.contest,
                'name': link.title_override or link.contest.name,
                'summary': link.summary or link.contest.summary,
                'problem_count': problem_count,
                'solved_count': solved_c,
                'progress_percent': progress_percent,
                'is_complete': problem_count > 0 and solved_c >= problem_count,
                # Empty contests stay locked for learners; roadmap editors can open the contest page.
                'is_locked': problem_count == 0 and not edit_roadmap,
                'index': index,
            })

        all_levels = list(RoadmapLevel.objects.filter(is_visible=True).order_by('order', 'level'))
        if can_edit_roadmap(self.request.user) and self.level not in all_levels:
            all_levels.append(self.level)
            all_levels.sort(key=lambda level: (level.order, level.level))

        assigned_contest_ids = set(self.level.roadmap_contests.values_list('contest_id', flat=True))
        available_contests = Contest.objects.order_by('-start_time', 'name')
        if assigned_contest_ids:
            available_contests = available_contests.exclude(id__in=assigned_contest_ids)

        context.update({
            'title': _('Roadmap - Level %(level)s') % {'level': self.level.level},
            'roadmap_level': self.level,
            'roadmap_entries': entries,
            'roadmap_levels': all_levels,
            'can_edit_roadmap': edit_roadmap,
            'edit_mode_enabled': edit_roadmap and self.request.GET.get('edit') == '1',
            'available_contests': available_contests[:200],
            'level_progress_solved': solved,
            'level_progress_total': total,
            'level_progress_percent': int((solved * 100 / total) if total else 0),
        })
        return context
