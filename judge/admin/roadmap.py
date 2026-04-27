from django.contrib import admin

from judge.models import RoadmapLevel, RoadmapLevelContest


class RoadmapLevelContestInline(admin.TabularInline):
    model = RoadmapLevelContest
    extra = 0
    raw_id_fields = ('contest',)
    ordering = ('order', 'id')
    fields = ('order', 'contest', 'title_override', 'summary')


class RoadmapLevelAdmin(admin.ModelAdmin):
    list_display = ('level', 'title', 'order', 'is_visible')
    list_editable = ('order', 'is_visible')
    ordering = ('order', 'level')
    prepopulated_fields = {'slug': ('title',)}
    inlines = (RoadmapLevelContestInline,)


class RoadmapLevelContestAdmin(admin.ModelAdmin):
    list_display = ('level', 'contest', 'order')
    list_editable = ('order',)
    ordering = ('level__order', 'order', 'id')
    raw_id_fields = ('level', 'contest')
