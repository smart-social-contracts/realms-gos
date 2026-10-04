import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Menu, X, LogIn, LogOut, User } from 'lucide-react'
import LanguageSwitcher from './LanguageSwitcher'
import { useAuth } from '../hooks/useAuth'

function SiteNav({ active = 'home' }) {
  const { t } = useTranslation()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const { isAuthenticated, principal, isLoading, login, logout } = useAuth()

  const sectionHref = (hash) => (active === 'home' ? hash : `/${hash}`)

  // All nav items share the same ink; only the current page is emphasized.
  const navLinkClass = (page) =>
    page === active
      ? 'text-primary-900 font-medium'
      : 'text-primary-900 hover:text-primary-700 transition-colors'

  const mobileNavLinkClass = (page) =>
    page === active
      ? 'block text-primary-900 font-medium'
      : 'block text-primary-900 hover:text-primary-700'

  const authDesktop = isLoading ? (
    <span className="text-slate-400 text-sm">...</span>
  ) : isAuthenticated ? (
    <div className="flex items-center gap-2">
      <span className="hidden lg:inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-primary-50 text-primary-700 text-xs font-mono">
        <User className="w-3 h-3" />
        {principal?.slice(0, 5)}...{principal?.slice(-3)}
      </span>
      <button
        onClick={logout}
        className="inline-flex items-center gap-1 px-3 py-2 text-sm text-slate-600 hover:text-slate-900 transition-colors"
        title={t('nav.logout', 'Log out')}
      >
        <LogOut className="w-4 h-4" />
      </button>
    </div>
  ) : (
    <button
      onClick={login}
      className="inline-flex items-center gap-2 px-4 py-2 bg-primary-900 text-white rounded-lg text-sm font-medium hover:bg-primary-800 transition-colors"
    >
      <LogIn className="w-4 h-4" />
      {t('nav.login', 'Log in')}
    </button>
  )

  const authMobile = isLoading ? (
    <span className="text-slate-400">...</span>
  ) : isAuthenticated ? (
    <div className="flex items-center justify-between pt-2 border-t border-slate-200">
      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-primary-50 text-primary-700 text-xs font-mono">
        <User className="w-3 h-3" />
        {principal?.slice(0, 5)}...{principal?.slice(-3)}
      </span>
      <button
        onClick={() => { logout(); setMobileMenuOpen(false) }}
        className="inline-flex items-center gap-1 px-3 py-2 text-sm text-slate-600 hover:text-slate-900"
      >
        <LogOut className="w-4 h-4" />
        {t('nav.logout', 'Log out')}
      </button>
    </div>
  ) : (
    <button
      onClick={() => { login(); setMobileMenuOpen(false) }}
      className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 bg-primary-900 text-white rounded-lg text-sm font-medium hover:bg-primary-800"
    >
      <LogIn className="w-4 h-4" />
      {t('nav.login', 'Log in')}
    </button>
  )

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 glass-dark shadow-sm">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <Link to="/" className="flex items-center gap-3">
            <img src="/logo_horizontal.svg" alt="Realms" className="h-10" />
          </Link>

          <div className="hidden md:flex items-center gap-8">
            <a href={sectionHref('#features')} className={navLinkClass()}>
              {t('nav.howItWorks')}
            </a>
            <Link to="/marketplace" className={navLinkClass('marketplace')}>
              {t('nav.marketplace')}
            </Link>
            <a href={sectionHref('#forpeople')} className={navLinkClass()}>
              {t('nav.forPeople')}
            </a>
            <a href={sectionHref('#forinstitutions')} className={navLinkClass()}>
              {t('nav.forInstitutions')}
            </a>
            <a
              href="https://github.com/smart-social-contracts/realms-gos/blob/main/ROADMAP.md"
              target="_blank"
              rel="noopener noreferrer"
              className={navLinkClass()}
            >
              {t('nav.roadmap')}
            </a>
            <LanguageSwitcher />
            {authDesktop}
          </div>

          <button className="md:hidden p-2" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}>
            {mobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
          </button>
        </div>
      </div>

      {mobileMenuOpen && (
        <div className="md:hidden glass-dark border-t border-slate-200">
          <div className="px-4 py-4 space-y-4">
            <a href={sectionHref('#features')} className={mobileNavLinkClass()}>
              {t('nav.howItWorks')}
            </a>
            <Link to="/marketplace" className={mobileNavLinkClass('marketplace')} onClick={() => setMobileMenuOpen(false)}>
              {t('nav.marketplace')}
            </Link>
            <a href={sectionHref('#forpeople')} className={mobileNavLinkClass()}>
              {t('nav.forPeople')}
            </a>
            <a href={sectionHref('#forinstitutions')} className={mobileNavLinkClass()}>
              {t('nav.forInstitutions')}
            </a>
            <a
              href="https://github.com/smart-social-contracts/realms-gos/blob/main/ROADMAP.md"
              target="_blank"
              rel="noopener noreferrer"
              className={mobileNavLinkClass()}
            >
              {t('nav.roadmap')}
            </a>
            <LanguageSwitcher />
            {authMobile}
          </div>
        </div>
      )}
    </nav>
  )
}

export default SiteNav
