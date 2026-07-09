"""Plain JSON serialization helpers for the Journal system.

No DRF is installed in this project — views build responses with
JsonResponse and dict literals (see `_serialize_journal` and friends in
views.py). These helpers follow the same convention so Phase 2 views can
reuse them.
"""


def serialize_attachment(attachment):
    return {
        'id':           attachment.id,
        'type':         attachment.attachment_type,
        'file':         attachment.file.url if attachment.file else None,
        'sticker_code': attachment.sticker_code,
        'label':        attachment.label,
        'order':        attachment.order,
        'position_x':   attachment.position_x,
        'position_y':   attachment.position_y,
        'width_pct':    attachment.width_pct,
        'rotation':     attachment.rotation,
        'created_at':   attachment.created_at.isoformat(),
    }


def serialize_journal_page(page, include_attachments=True):
    data = {
        'id':             page.id,
        'journal_id':     page.journal_id,
        'page_number':    page.page_number,
        'content':        page.content,
        'mood':           page.mood,
        'date':           page.date.isoformat(),
        'day_of_week':    page.day_of_week,
        'created_at':     page.created_at.isoformat(),
        'updated_at':     page.updated_at.isoformat(),
        'prompt':         page.prompt,
        'expires_at':     page.expires_at.isoformat() if page.expires_at else None,
        'is_archived':    page.is_archived,
        'is_locked':      page.is_locked,
        'is_released':    page.is_released,
        'released_at':    page.released_at.isoformat() if page.released_at else None,
        'release_ritual': page.release_ritual,
    }
    if include_attachments and not page.is_released:
        data['attachments'] = [serialize_attachment(a) for a in page.attachments.all()]
    else:
        data['attachments'] = []
    return data


def serialize_journal(journal, include_pages=False):
    data = {
        'id':           journal.id,
        'kind':         journal.kind,
        'title':        journal.title,
        'icon':         journal.icon,
        'color':        journal.color,
        'color_hex':    journal.color_hex,
        'cover_style':  journal.cover_style,
        'is_locked':    journal.is_locked,
        'has_password': journal.has_password,
        'created_at':   journal.created_at.isoformat(),
        'updated_at':   journal.updated_at.isoformat(),
        'last_opened':  journal.last_opened.isoformat() if journal.last_opened else None,
        'page_count':   journal.pages.count(),
    }
    if include_pages:
        data['pages'] = [serialize_journal_page(p) for p in journal.pages.all()]
    return data
