// SANA — Service Worker for Web Push Notifications

self.addEventListener('push', (event) => {
    let data = { title: 'SANA', body: 'Nouvelle notification', url: '/dashboard/' };
    try { data = JSON.parse(event.data.text()); } catch (e) {}

    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            tag: 'sana-notif',
            data: { url: data.url },
            vibrate: [200, 100, 200],
            requireInteraction: false,
        })
    );
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const url = event.notification.data?.url || '/dashboard/';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if ('focus' in client) return client.navigate(url).then(c => c.focus());
            }
            if (clients.openWindow) return clients.openWindow(url);
        })
    );
});
