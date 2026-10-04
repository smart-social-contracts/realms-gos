#!/usr/bin/env node
/**
 * Scaffold a sandboxed Realms extension from template/.
 * Invoked by: npm create @realmsgos/extension [target-dir] [-- flags]
 */
import {
	cpSync,
	existsSync,
	mkdirSync,
	readdirSync,
	readFileSync,
	statSync,
	writeFileSync,
} from 'node:fs';
import { basename, dirname, extname, join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const TEMPLATE_DIR = join(__dirname, '..', 'template');
const DOCS_URL =
	'https://github.com/smart-social-contracts/realms-gos/blob/main/docs/guide/extension-authoring.md';

const ID_PATTERN = /^[a-z][a-z0-9_]{2,31}$/;
const TEXT_EXTENSIONS = new Set([
	'.css',
	'.html',
	'.js',
	'.json',
	'.md',
	'.mjs',
	'.py',
	'.svelte',
	'.ts',
]);

function splitBasename(name) {
	const withSpaces = name.replace(/([a-z])([A-Z])/g, '$1 $2');
	return withSpaces.split(/[^a-zA-Z0-9]+/).filter(Boolean);
}

function basenameToId(name) {
	return splitBasename(name)
		.map((part) => part.toLowerCase())
		.join('_');
}

function basenameToName(name) {
	return splitBasename(name)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
		.join(' ');
}

function usage() {
	console.log(`Usage: create-realms-extension [target-dir] [options]

When target-dir is given, --id and --name default from its basename
(e.g. demo-greeter → id demo_greeter, name "Demo Greeter").

Options:
  --id <snake_case_id>       Extension identifier (default: derived from target-dir)
  --name "<Display Name>"    Sidebar / page title (default: derived from target-dir)
  --description "..."        Short description for manifest.json
  -h, --help                 Show this help
`);
}

function parseArgs(argv) {
	const positional = [];
	const flags = { id: '', name: '', description: '' };

	for (let i = 0; i < argv.length; i++) {
		const arg = argv[i];
		if (arg === '-h' || arg === '--help') {
			flags.help = true;
			continue;
		}
		if (arg === '--id') {
			flags.id = argv[++i] ?? '';
			continue;
		}
		if (arg === '--name') {
			flags.name = argv[++i] ?? '';
			continue;
		}
		if (arg === '--description') {
			flags.description = argv[++i] ?? '';
			continue;
		}
		if (arg.startsWith('--')) {
			console.error(`Unknown option: ${arg}`);
			usage();
			process.exit(1);
		}
		positional.push(arg);
	}

	return { positional, flags };
}

function ask(rl, question, defaultValue = '') {
	const hint = defaultValue ? ` (${defaultValue})` : '';
	return new Promise((resolvePrompt) => {
		rl.question(`${question}${hint}: `, (answer) => {
			const trimmed = answer.trim();
			resolvePrompt(trimmed || defaultValue);
		});
	});
}

function validateId(id) {
	if (!ID_PATTERN.test(id)) {
		console.error('Error: extension id must match ^[a-z][a-z0-9_]{2,31}$');
		console.error(
			'  Use a lowercase letter first, then 2–31 lowercase letters, digits, or underscores.',
		);
		process.exit(1);
	}
}

function isTextFile(filePath) {
	return TEXT_EXTENSIONS.has(extname(filePath).toLowerCase());
}

function substitute(content, vars) {
	return content
		.replaceAll('__EXTENSION_ID__', vars.id)
		.replaceAll('__EXTENSION_NAME__', vars.name)
		.replaceAll('__EXTENSION_DESCRIPTION__', vars.description);
}

function assertDirEmpty(targetDir) {
	if (!existsSync(targetDir)) {
		return;
	}
	const entries = readdirSync(targetDir);
	if (entries.length > 0) {
		console.error(`Error: target directory is not empty: ${targetDir}`);
		process.exit(1);
	}
}

function copyTemplate(srcDir, destDir, vars) {
	mkdirSync(destDir, { recursive: true });
	for (const entry of readdirSync(srcDir, { withFileTypes: true })) {
		const srcPath = join(srcDir, entry.name);
		const destPath = join(destDir, entry.name);
		if (entry.isDirectory()) {
			copyTemplate(srcPath, destPath, vars);
			continue;
		}
		if (isTextFile(srcPath)) {
			const content = readFileSync(srcPath, 'utf8');
			writeFileSync(destPath, substitute(content, vars), 'utf8');
		} else {
			cpSync(srcPath, destPath);
		}
	}
}

function defaultDescription(name) {
	return `${name} — a sandboxed Realms extension`;
}

async function main() {
	const { positional, flags } = parseArgs(process.argv.slice(2));

	if (flags.help) {
		usage();
		return;
	}

	const rl = createInterface({ input: process.stdin, output: process.stdout });
	const interactive = process.stdin.isTTY && process.stdout.isTTY;

	let targetDir = positional[0] ?? '';
	if (!targetDir && interactive) {
		targetDir = await ask(rl, 'Target directory', 'my-extension');
	} else if (!targetDir) {
		console.error('Error: target directory is required (or run interactively).');
		usage();
		process.exit(1);
	}

	targetDir = resolve(process.cwd(), targetDir);

	const dirBasename = basename(targetDir);

	let id = flags.id;
	if (!id && interactive) {
		id = await ask(rl, 'Extension id (snake_case)', basenameToId(dirBasename));
	} else if (!id) {
		id = basenameToId(dirBasename);
	}
	validateId(id);

	let name = flags.name;
	if (!name && interactive) {
		name = await ask(rl, 'Display name', basenameToName(dirBasename));
	} else if (!name) {
		name = basenameToName(dirBasename);
	}

	let description = flags.description;
	if (!description && interactive) {
		description = await ask(rl, 'Description', defaultDescription(name));
	} else if (!description) {
		description = defaultDescription(name);
	}

	rl.close();

	assertDirEmpty(targetDir);

	if (!existsSync(TEMPLATE_DIR) || !statSync(TEMPLATE_DIR).isDirectory()) {
		console.error(`Error: template directory missing: ${TEMPLATE_DIR}`);
		process.exit(1);
	}

	const vars = { id, name, description };
	copyTemplate(TEMPLATE_DIR, targetDir, vars);

	console.log(`\nCreated sandboxed Realms extension in ${targetDir}\n`);
	console.log('Next steps:');
	console.log(`  cd ${targetDir === process.cwd() ? basename(targetDir) : targetDir}`);
	console.log('  cd frontend && npm install');
	console.log('  npm run build');
	console.log(`\nAuthoring guide: ${DOCS_URL}\n`);
}

main().catch((err) => {
	console.error(err instanceof Error ? err.message : err);
	process.exit(1);
});
