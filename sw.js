// sw.js — Push Notification Service Worker
// Served at /sw.js (root scope so it controls all pages on this origin)

const SW_VERSION = 'push-service-v1';

// ─── Install ──────────────────────────────────────────────────────────────────
self.addEventListener('install', event => {
  console.log('[SW] Installing', SW_VERSION);
  self.skipWaiting(); // activate immediately, don't wait for old SW to stop
});

// ─── Activate ─────────────────────────────────────────────────────────────────
self.addEventListener('activate', event => {
  console.log('[SW] Activating', SW_VERSION);
  event.waitUntil(self.clients.claim()); // take control of all open tabs immediately
});

// ─── Push ─────────────────────────────────────────────────────────────────────
self.addEventListener('push', event => {
  console.log('[SW] ════════════════════════════════════');
  console.log('[SW] Push event received');

  let data = {};

  if (event.data) {
    try {
      data = event.data.json();
      console.log('[SW] Parsed push payload:', data);
    } catch (err) {
      // Fallback: treat raw text as the notification body
      console.warn('[SW] Could not parse push data as JSON, using raw text');
      data = { title: 'New notification', body: event.data.text() };
    }
  } else {
    console.warn('[SW] Push event had no data');
    data = { title: 'New notification', body: '' };
  }

  const title   = data.title  || 'Notification';
  const options = {
    body:    data.body   || '',
    icon:    data.icon   || '/static/icons/notification.png',
    badge:   data.badge  || '/static/icons/badge.png',
    tag:     data.tag    || 'push-notification',   // replaces previous notification with same tag
    renotify: !!data.tag,                           // vibrate even if replacing
    data:    data.data   || {},                     // arbitrary payload passed to notificationclick
    actions: data.actions || [],
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
      .then(() => {
        console.log('[SW] Notification shown:', title);

        // Notify any open clients so they can update UI
        return self.clients.matchAll({ includeUncontrolled: true, type: 'window' });
      })
      .then(clients => {
        clients.forEach(client =>
          client.postMessage({ type: 'PUSH_RECEIVED', title, body: options.body, data: options.data })
        );
      })
      .catch(err => {
        console.error('[SW] Failed to show notification:', err);
      })
  );
});

// ─── Notification click ───────────────────────────────────────────────────────
self.addEventListener('notificationclick', event => {
  console.log('[SW] Notification clicked, action:', event.action);
  event.notification.close();

  const url = event.notification.data?.url || '/';

  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then(clients => {
        // Focus an existing tab showing this URL if possible
        const existing = clients.find(c => c.url === url || c.url.startsWith(self.location.origin));
        if (existing) {
          return existing.focus();
        }
        // Otherwise open a new tab
        return self.clients.openWindow(url);
      })
  );
});

// ─── Notification close ───────────────────────────────────────────────────────
self.addEventListener('notificationclose', event => {
  console.log('[SW] Notification dismissed');
});

// ─── Push subscription change ─────────────────────────────────────────────────
// Fired when the push service invalidates a subscription (e.g. user cleared site data)
self.addEventListener('pushsubscriptionchange', event => {
  console.log('[SW] Push subscription changed — re-subscribing');

  event.waitUntil(
    self.registration.pushManager.subscribe(event.oldSubscription.options)
      .then(newSubscription => {
        // Notify open clients so they can re-register the new subscription with the server
        return self.clients.matchAll({ includeUncontrolled: true, type: 'window' })
          .then(clients => {
            clients.forEach(client =>
              client.postMessage({ type: 'SUBSCRIPTION_CHANGED', subscription: newSubscription.toJSON() })
            );
          });
      })
      .catch(err => console.error('[SW] Failed to re-subscribe:', err))
  );
});
