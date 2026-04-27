from django.core.validators import RegexValidator
from django.db import models
from django.db.models import CASCADE
from django.utils.translation import gettext_lazy as _

from judge.models.contest import Contest

__all__ = ['RoadmapLevel', 'RoadmapLevelContest']


class RoadmapLevel(models.Model):
    color_validator = RegexValidator('^#(?:[A-Fa-f0-9]{3}){1,2}$', _('Invalid colour.'))

    level = models.PositiveSmallIntegerField(unique=True, db_index=True, verbose_name=_('level number'))
    slug = models.SlugField(unique=True, verbose_name=_('slug'))
    title = models.CharField(max_length=120, verbose_name=_('title'))
    subtitle = models.CharField(max_length=180, blank=True, verbose_name=_('subtitle'))
    description = models.TextField(blank=True, verbose_name=_('description'))
    accent_color = models.CharField(max_length=7, default='#8da2c7', validators=[color_validator],
                                    verbose_name=_('accent colour'))
    order = models.PositiveIntegerField(default=0, db_index=True, verbose_name=_('order'))
    is_visible = models.BooleanField(default=True, verbose_name=_('publicly visible'))
    callout_title = models.CharField(max_length=140, blank=True, verbose_name=_('callout title'))
    callout_content = models.TextField(blank=True, verbose_name=_('callout content'))

    class Meta:
        ordering = ('order', 'level')
        permissions = (
            ('view_roadmap', _('View roadmap')),
            ('roadmap_level_edit_mode', _('Use roadmap level edit mode')),
        )
        verbose_name = _('roadmap level')
        verbose_name_plural = _('roadmap levels')

    def __str__(self):
        return f'Level {self.level}: {self.title}'


class RoadmapLevelContest(models.Model):
    level = models.ForeignKey(RoadmapLevel, related_name='roadmap_contests', on_delete=CASCADE,
                              verbose_name=_('level'))
    contest = models.ForeignKey(Contest, related_name='roadmap_links', on_delete=CASCADE,
                                verbose_name=_('contest'))
    order = models.PositiveIntegerField(default=0, db_index=True, verbose_name=_('order'))
    title_override = models.CharField(max_length=180, blank=True, verbose_name=_('title override'))
    summary = models.CharField(max_length=220, blank=True, verbose_name=_('summary'))

    class Meta:
        ordering = ('order', 'id')
        unique_together = (('level', 'contest'),)
        verbose_name = _('roadmap contest')
        verbose_name_plural = _('roadmap contests')

    def __str__(self):
        return f'Level {self.level.level} - {self.contest.name}'
