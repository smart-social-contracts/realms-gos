import { test, expect } from '@playwright/test';

// The assistant's service, ggg-ai, one for every environment. It answers every route but
// the health check, the persona list and the two sign-in routes only to a signed-in user,
// so this checks what a visitor can: it is up, it offers sign-in, and it still refuses an
// anonymous question. It cannot tell whether a page manages to sign in (that needs a real
// Internet Identity), so a client that never asks for a session passes this check.
const ASSISTANT_API = 'https://geister-api.realmsgos.dev';

test.skip(
	process.env.SMOKE_ENV === 'staging',
	'one service for every environment: checked with production'
);

test('the assistant service is up and asks for sign-in', async ({ request }) => {
	const health = await request.get(`${ASSISTANT_API}/`);
	expect(health.status(), 'GET /').toBe(200);
	expect(await health.json()).toMatchObject({ service: 'ggg-ai', status: 'ok' });

	const personas = await request.get(`${ASSISTANT_API}/api/personas/assistants`);
	expect(personas.status(), 'GET /api/personas/assistants').toBe(200);
	expect((await personas.json()).assistants.map((a: { id: string }) => a.id)).toContain('ashoka');

	const challenge = await request.get(`${ASSISTANT_API}/api/auth/challenge`);
	expect(challenge.status(), 'GET /api/auth/challenge').toBe(200);
	expect(await challenge.json()).toHaveProperty('nonce');

	const ask = await request.post(`${ASSISTANT_API}/api/ask`, { data: { question: 'hello' } });
	expect(ask.status(), 'POST /api/ask without a session').toBe(401);
});
