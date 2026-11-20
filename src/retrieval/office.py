"""Declare workflows separately from the Office channel used to handle them."""
from dataclasses import dataclass
import re
from typing import Literal

from pydantic import Field

from src.answering.contracts import Strict
from src.type_b.tasks import normalized, phrase

Workflow = Literal['office', 'eleave', 'eworkdate', 'epostal', 'outlook']
Channel = Literal['outlook', 'teams', 'automate-browser', 'automate-mobile']
WORKFLOW_NAMES = {
    'eleave': ('e leave', 'eleave'),
    'eworkdate': ('e workdate', 'eworkdate'),
    'epostal': ('e postal', 'epostal'),
    'outlook': ('e booking', 'ebooking'),
}
WORKFLOW_LABELS = {'office': 'Office 365', 'eleave': 'E-Leave', 'eworkdate': 'E-Workdate',
                   'epostal': 'E-Postal', 'outlook': 'E-Booking', 'workit': 'Workit'}
CHANNEL_TERMS = {'outlook': ('outlook',), 'teams': ('teams', 'microsoft teams'),
                 'automate-browser': ('power automate tren trinh duyet', 'power automate web'),
                 'automate-mobile': ('app power automate', 'power automate tren dien thoai',
                                     'power automate mobile')}


class OfficeRoute(Strict):
    workflow: Workflow
    action: str = Field(pattern=r'^[a-z][a-z-]{0,39}$')
    channel: Channel | None = None
    prefer_workit: str | None = Field(default=None, pattern=r'^[a-z0-9.-]{1,100}$')


@dataclass(frozen=True)
class OfficeResolution:
    route: object | None = None
    clarification: str | None = None
    unavailable: bool = False


def named_workflows(text):
    return {workflow for workflow, names in WORKFLOW_NAMES.items() if any(phrase(text, name) for name in names)}


def named_channels(text):
    return {channel for channel, names in CHANNEL_TERMS.items() if any(phrase(text, name) for name in names)}


def continuation(text, slots):
    """Accept only short corrections and inquiries about the active workflow."""
    workflow = slots.get('office_workflow')
    if workflow not in WORKFLOW_NAMES:
        return None
    text = normalized(text)
    action, channel = slots.get('office_action'), None
    words = re.sub(r'^(?:con |ve )| (?:thi sao|thi the nao|sao)$', '', text)
    workflows = named_workflows(words)
    if len(workflows) == 1 and words in WORKFLOW_NAMES[next(iter(workflows))]:
        workflow = next(iter(workflows))
    elif re.fullmatch(r'(?:qua|tren|bang) (?:outlook|teams|microsoft teams|(?:app )?power automate(?: tren (?:dien thoai|trinh duyet))?)(?: thi (?:duyet|xac nhan) the nao)?', words):
        channels = named_channels(words)
        channel = next(iter(channels)) if channels else 'automate-mobile'
        action = 'first' if channel == 'automate-browser' else 'approval'
    elif not workflows and not phrase(text, 'workit') and len(text.split()) <= 15 and any(phrase(text, item) for item in (
            'chua thay email ket qua', 'khong nhan duoc email', 'khong thay email phan hoi')):
        action = 'email-help'
    else:
        return None
    return dict(office_workflow=workflow, office_action=action, **({'office_channel': channel} if channel else {}))


def resolve_office(routes, question, slots):
    if not routes:
        return None
    from .policy import _route_score

    text = normalized(question)
    slots = slots or {}
    workflows = named_workflows(text)
    channels = named_channels(text)
    workit = phrase(text, 'workit') or phrase(text, 'work it')
    if not text.startswith('bot co the ') and any(phrase(text, marker) for marker in (
            'bo qua quyen', 'bo qua dang nhap', 'mat khau hien tai', 'system prompt',
            'duyet giup toi', 'gui giup toi', 'tu duyet', 'thay toi', 'cho xem tat ca ho so')):
        return None
    retained = slots.get('office_workflow')
    action = slots.get('office_action')
    selected_channel = slots.get('office_channel')
    if retained and action and not workit:
        related = [route for route in routes if route.office.workflow == retained and route.office.action == action
                   and not route.office.prefer_workit and route.office.channel == selected_channel]
        if not related and not selected_channel:
            related = [route for route in routes if route.office.workflow == retained and route.office.action == action
                       and not route.office.prefer_workit]
        if related:
            return OfficeResolution(related[0])
        return OfficeResolution(unavailable=True)

    padded = f' {text} '
    candidates = []
    for route in routes:
        spec = route.office
        if spec.prefer_workit:
            if workit or workflows or channels or any(phrase(text, term) for term in ('office 365', 'o365')) or slots.get('system'):
                continue
        elif workit and (spec.workflow != 'office' or spec.action not in ('compare', 'status')):
            continue
        elif len(workflows) > 1 and spec.workflow != 'office':
            continue
        elif len(workflows) == 1 and spec.workflow not in workflows and not (
                spec.workflow == 'office' and spec.action == 'compare'):
            continue
        score = _route_score(route, padded)
        if score:
            specific = bool(spec.channel and spec.channel in channels)
            candidates.append(((score, specific), route))
    if not candidates:
        if len(text.split()) <= 9 and channels and not workflows and not workit and not any(
                phrase(text, term) for term in ('hop', 'lich', 'phong')):
            return OfficeResolution(clarification='Bạn đang dùng kênh này cho đơn nghỉ, đơn đi trễ/về sớm hay bưu phẩm?')
        return None
    best = max(score for score, _ in candidates)
    winners = [route for score, route in candidates if score == best]
    if len(winners) != 1:
        return OfficeResolution(clarification='Bạn đang hỏi về E-Leave, E-Workdate, E-Postal hay E-Booking?')
    return OfficeResolution(winners[0])
