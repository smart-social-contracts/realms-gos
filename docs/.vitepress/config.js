import { defineConfig } from 'vitepress'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))

// Read version from pyproject.toml
function getVersion() {
  try {
    const pyprojectPath = resolve(__dirname, '../../cli/pyproject.toml')
    const pyprojectContent = readFileSync(pyprojectPath, 'utf-8')
    const versionMatch = pyprojectContent.match(/version\s*=\s*"([^"]+)"/)
    return versionMatch ? versionMatch[1] : '0.0.0'
  } catch (e) {
    console.warn('Could not read version from pyproject.toml:', e.message)
    return '0.0.0'
  }
}

const version = getVersion()

export default defineConfig({
  vite: {
    build: {
      target: 'es2020',
    },
  },
  title: 'Realms',
  description: 'Governance Operating System Documentation',
  base: '/docs/',
  ignoreDeadLinks: true,
  appearance: true,
  
  head: [
    ['link', { rel: 'icon', href: '/docs/img/logo_sphere_only.svg' }]
  ],

  locales: {
    root: {
      label: 'English',
      lang: 'en'
    }
  },

  themeConfig: {
    logo: '/img/logo_horizontal.svg',
    siteTitle: false,
    
    darkModeSwitchLabel: 'Appearance',
    lightModeSwitchTitle: 'Switch to light mode',
    darkModeSwitchTitle: 'Switch to dark mode',
    
    nav: [
      { text: 'Guide', link: '/guide/getting-started' },
      { text: 'Reference', link: '/reference/' },
      { text: 'Extensions', link: '/extensions/' },
      { 
        text: `v${version}`,
        items: [
          { text: 'Changelog', link: 'https://github.com/smart-social-contracts/realms-gos/releases' },
          { text: `Current: v${version}`, link: '#' }
        ]
      }
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'Introduction',
          items: [
            { text: 'Getting Started', link: '/guide/getting-started' },
            { text: 'What is Realms?', link: '/reference/NON_TECHNICAL_INTRO' },
            { text: 'Technical Overview', link: '/reference/TECHNICAL_INTRO' }
          ]
        },
        {
          text: 'Quick Start',
          items: [
            { text: 'Casals Rollout', link: '/reference/CASALS_ROLLOUT' },
            { text: 'CLI Reference', link: '/reference/CLI_REFERENCE' },
            { text: 'Governance Tutorial', link: '/reference/GOVERNANCE_TUTORIAL' }
          ]
        }
      ],
      '/reference/': [
        {
          text: 'Core Concepts',
          items: [
            { text: 'Overview', link: '/reference/' },
            { text: 'Core Entities', link: '/reference/CORE_ENTITIES' },
            { text: 'API Reference', link: '/reference/API_REFERENCE' },
            { text: 'CLI Reference', link: '/reference/CLI_REFERENCE' }
          ]
        },
        {
          text: 'Architecture',
          items: [
            { text: 'Canister Diagram', link: '/reference/TECHNICAL_INTRO#canister-diagram' },
            { text: 'Frontend Architecture', link: '/reference/FRONTEND_ARCHITECTURE' },
            { text: 'Extension Architecture', link: '/reference/EXTENSION_ARCHITECTURE' },
            { text: 'Method Override System', link: '/reference/METHOD_OVERRIDE_SYSTEM' }
          ]
        },
        {
          text: 'Advanced Features',
          items: [
            { text: 'Scheduled Tasks', link: '/reference/SCHEDULED_TASKS' },
            { text: 'Task Entity', link: '/reference/TASK_ENTITY' },
            { text: 'Multi-Step Tasks', link: '/reference/MULTI_STEP_TASKS_IMPLEMENTATION' }
          ]
        },
        {
          text: 'Operations',
          items: [
            { text: 'Casals Rollout', link: '/reference/CASALS_ROLLOUT' },
            { text: 'Realm Registration', link: '/reference/REALM_REGISTRATION_GUIDE' },
            { text: 'Troubleshooting', link: '/reference/TROUBLESHOOTING' }
          ]
        },
        {
          text: 'Examples',
          items: [
            { text: 'Demo Example', link: '/reference/EXAMPLE_DEMO' },
            { text: 'File Download', link: '/reference/EXAMPLE_FILE_DOWNLOAD' }
          ]
        }
      ]
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/smart-social-contracts/realms-gos' }
    ],

    footer: {
      message: 'Open Source • MIT License',
      copyright: `Documentation for Realms v${version}`
    },

    editLink: {
      pattern: 'https://github.com/smart-social-contracts/realms-gos/edit/main/docs/:path',
      text: 'Edit this page on GitHub'
    }
  }
})
