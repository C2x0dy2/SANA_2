import json
import base64
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings


def send_notification(user, notif_type, title, body, url='/'):
    """Create a DB notification and push it via WebSocket + Web Push."""
    from .models import Notification, PushSubscription

    notif = Notification.objects.create(
        user=user, type=notif_type, title=title, body=body, url=url
    )
    data = {
        'id': notif.id,
        'type': notif_type,
        'title': title,
        'body': body,
        'url': url,
        'created_at': notif.created_at.isoformat(),
    }

    # WebSocket (user online)
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'notifications_{user.id}',
            {'type': 'send_notification', 'data': data},
        )
    except Exception:
        pass

    # Web Push (user offline)
    _send_web_push(user, title, body, url)

    return notif


def _send_web_push(user, title, body, url):
    try:
        from pywebpush import webpush, WebPushException
        from py_vapid import Vapid

        vapid_private_b64 = getattr(settings, 'VAPID_PRIVATE_KEY', '')
        vapid_email       = getattr(settings, 'VAPID_EMAIL', '')
        if not vapid_private_b64 or not vapid_email:
            return

        priv_pem = base64.b64decode(vapid_private_b64)
        vapid_obj = Vapid.from_pem(priv_pem)

        from .models import PushSubscription
        for sub in PushSubscription.objects.filter(user=user):
            try:
                webpush(
                    subscription_info={
                        'endpoint': sub.endpoint,
                        'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
                    },
                    data=json.dumps({'title': title, 'body': body, 'url': url}),
                    vapid_private_key=vapid_obj,
                    vapid_claims={'sub': f'mailto:{vapid_email}'},
                )
            except WebPushException as e:
                if '410' in str(e) or '404' in str(e):
                    sub.delete()
    except Exception:
        pass
