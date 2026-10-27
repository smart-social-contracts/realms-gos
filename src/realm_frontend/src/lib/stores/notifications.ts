import { get, writable } from 'svelte/store';
import { read, run, must } from '$lib/realm';
import { settleAuthSession } from '$lib/auth.js';
import { isAuthenticated } from '$lib/stores/auth.js';

export interface NotificationItem {
    id: string;
    title: string;
    message: string;
    timestamp_ms: number;
    read: boolean;
    sender?: string;
    sender_name?: string;
    audience_type?: string;
    department?: string;
    icon?: string;
    href?: string;
    color?: string;
}

export const notifications = writable<NotificationItem[]>([]);
export const unreadCount = writable<number>(0);

function setRead(id: string | null, read: boolean) {
    notifications.update(items => {
        const updated = items.map(item => (id === null || item.id === id ? { ...item, read } : item));
        unreadCount.set(updated.filter(n => !n.read).length);
        return updated;
    });
}

export async function loadNotifications() {
    try {
        await settleAuthSession();
        if (!get(isAuthenticated)) {
            notifications.set([]);
            unreadCount.set(0);
            return;
        }
        const items: NotificationItem[] = [];
        let from: number | null = 1;
        while (from) {
            const page = await must(read('notification_list', { from, count: 100 }));
            for (const n of page.notifications) {
                items.push({
                    id: n.id,
                    title: n.title,
                    message: n.body,
                    timestamp_ms: n.at * 1000,
                    read: n.read,
                    sender: n.sender,
                    href: n.href
                });
            }
            from = page.next;
        }
        notifications.set(items);
        unreadCount.set(items.filter(n => !n.read).length);
    } catch (error) {
        console.error('[notifications] Failed to load notifications:', error);
        notifications.set([]);
        unreadCount.set(0);
    }
}

/** The realm only records reads: marking a notification unread changes this list until the next load. */
export async function markAsRead(notificationId: string, read: boolean = true) {
    try {
        if (read) await must(run('notification_read', { notification: notificationId }));
    } catch (error) {
        console.error('Failed to update notification read status:', error);
    }
    setRead(notificationId, read);
}

export async function markAllAsRead() {
    try {
        let from: number | null = 1;
        while (from) {
            from = (await must(run('notification_read', { all: true, from, count: 500 }))).next;
        }
    } catch (error) {
        console.error('Failed to mark all notifications as read:', error);
    }
    setRead(null, true);
}
