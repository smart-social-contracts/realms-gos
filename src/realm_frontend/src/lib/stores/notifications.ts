import { writable } from 'svelte/store';
// @ts-ignore
import { backend } from '$lib/canisters';
import { singleFlight } from '../utils/singleFlight';
import { callExtensionFunction, extensionQueryFunctions } from '../utils/extension-query';
import { peekExtensionManifest } from '../utils/extension-manifest';

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

// The sidebar layout reloads on mount and on every navigation, so the first
// render fired two identical (update) calls; share the one in flight.
const sharedLoad = singleFlight(fetchNotifications);

export function loadNotifications(): Promise<void> {
    return sharedLoad();
}

async function fetchNotifications(): Promise<void> {
    try {
        console.log('[notifications] Loading notifications...');
        // Candid signature is positional: (extension_name, function_name, args)
        const result = await callExtensionFunction(
            backend,
            'notifications',
            'get_notifications',
            '{}',
            extensionQueryFunctions(peekExtensionManifest('notifications')),
        ) as any;
        console.log('[notifications] Raw result:', result);
        // ExtensionCallResponse is { response: string, success: bool }
        const responseStr = result.response || result;
        console.log('[notifications] Response string:', responseStr);
        const notificationData = typeof responseStr === 'string' ? JSON.parse(responseStr) : responseStr;
        console.log('[notifications] Parsed data:', notificationData);
        console.log('[notifications] Notifications array:', notificationData.notifications);
        console.log('[notifications] Unread count:', notificationData.unread_count);
        notifications.set(notificationData.notifications || []);
        unreadCount.set(notificationData.unread_count || 0);
    } catch (error) {
        console.error('[notifications] Failed to load notifications:', error);
        // Set empty data on error
        notifications.set([]);
        unreadCount.set(0);
    }
}

export async function markAsRead(notificationId: string, read: boolean = true) {
    try {
        await backend.extension_sync_call(
            'notifications',
            'mark_as_read',
            JSON.stringify({ id: notificationId, read })
        );
        
        notifications.update(items => {
            const updated = items.map(item => 
                item.id === notificationId ? { ...item, read } : item
            );
            unreadCount.set(updated.filter(n => !n.read).length);
            return updated;
        });
    } catch (error) {
        console.error('Failed to update notification read status:', error);
        notifications.update(items => {
            const updated = items.map(item => 
                item.id === notificationId ? { ...item, read } : item
            );
            unreadCount.set(updated.filter(n => !n.read).length);
            return updated;
        });
    }
}

export async function markAllAsRead() {
    let unread: NotificationItem[] = [];
    notifications.update((items) => {
        unread = items.filter((n) => !n.read);
        return items;
    });
    for (const item of unread) {
        await markAsRead(item.id, true);
    }
}
