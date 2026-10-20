from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from briefs.models import Brief, SemanticMatchCache
from .models import Moodboard, Element


MAX_ELEMENTS = 36


def bump(board):
    board.revision += 1
    board.save(update_fields=['revision', 'updated_at'])


def check_revision(board, revision):
    if board.revision != revision:
        raise ValidationError('This board changed in another tab. Reload before saving.')


def direction_cards(board):
    return [element.as_card() for element in board.elements.all()]


def create_from_idea(user, idea, direction=None):
    with transaction.atomic():
        board = Moodboard.objects.create(owner=user, idea=idea,
            title=direction['title'] if direction else 'Untitled creative direction',
            visual_style=direction['visual_style'] if direction else '',
            source='GEMINI' if direction else 'MANUAL')
        cards = []
        if direction:
            cards.extend({'kind': 'COLOR', 'title': c['label'], 'color': c['hex']} for c in direction['palette'])
            cards.append({'kind': 'MOOD', 'title': 'The atmosphere', 'text': direction['mood']})
            for key, kind, label in [('shots', 'SHOT', 'Shot'), ('references', 'REFERENCE', 'Reference concept'), ('deliverables', 'DELIVERABLE', 'Deliverable')]:
                cards.extend({'kind': kind, 'title': f'{label} {i + 1}', 'text': text} for i, text in enumerate(direction[key]))
        else:
            cards = [{'kind': 'NOTE', 'title': 'The starting idea', 'text': idea[:1500]},
                     {'kind': 'MOOD', 'title': 'Set the atmosphere', 'text': 'Describe the feeling, lighting, and texture you want.'}]
        Element.objects.bulk_create([Element(board=board, position=i, **card) for i, card in enumerate(cards)])
        return board


def approve(board):
    """Caller locks the board. A snapshot keeps subsequent draft edits private."""
    if not board.brief_id or not board.visual_style.strip() or not board.elements.exists():
        raise ValidationError('Link or create a brief, select a visual style, and add a card before approval.')
    brief = Brief.objects.select_for_update().get(pk=board.brief_id, brand_id=board.owner_id)
    board.approved_snapshot = {'title': board.title, 'visual_style': board.visual_style,
                               'cards': direction_cards(board), 'source': board.source}
    board.approved_revision = board.revision
    board.approved_at = timezone.now()
    board.save(update_fields=['approved_snapshot', 'approved_revision', 'approved_at', 'updated_at'])
    brief.style = board.visual_style
    brief.save(update_fields=['style', 'updated_at'])
    SemanticMatchCache.objects.filter(brief=brief).delete()


def brief_initial(board):
    cards = direction_cards(board)
    direction = '\n\n'.join(f"{c['title']}: {c['text']}" for c in cards if c['text'] and c['kind'] != 'DELIVERABLE')
    deliverables = '\n'.join(c['text'] or c['title'] for c in cards if c['kind'] == 'DELIVERABLE')
    return {'title': board.title, 'style': board.visual_style,
            'description': (board.idea + '\n\n' + direction).strip()[:5000],
            'deliverables': deliverables[:2000], 'status': 'DRAFT'}
