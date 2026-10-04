import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import SiteNav from '../components/SiteNav'
import { 
  Globe, 
  Shield, 
  Code, 
  Layers, 
  ArrowRight, 
  CheckCircle, 
  Terminal, 
  Github, 
  ExternalLink,
  Zap,
  Users,
  Building2,
  Vote,
  Wallet,
  Scale,
  BookOpen,
  ChevronRight,
  X,
  Fingerprint,
  Landmark,
  UserPlus,
  HeartHandshake,
  Bot,
  Mail
} from 'lucide-react'

function LandingPage() {
  const { t } = useTranslation();
  const [lightboxImage, setLightboxImage] = useState(null)

  const features = [
    {
      icon: <img src="/images/icp_logo_bw.png" alt="ICP" className="w-6 h-6" />,
      titleKey: "features.internetComputer.title",
      descKey: "features.internetComputer.description",
      image: "/images/ic_dashboard.png"
    },
    {
      icon: <Shield className="w-6 h-6" />,
      titleKey: "features.secure.title",
      descKey: "features.secure.description"
    },
    {
      icon: <Code className="w-6 h-6" />,
      titleKey: "features.developerFriendly.title",
      descKey: "features.developerFriendly.description"
    },
    {
      icon: <Users className="w-6 h-6" />,
      titleKey: "features.multiRealm.title",
      descKey: "features.multiRealm.description",
      image: "/images/mundus_map.png"
    },
    {
      icon: <Layers className="w-6 h-6" />,
      titleKey: "features.extensionSystem.title",
      descKey: "features.extensionSystem.description",
      image: "/images/marketplace.png"
    },
    {
      icon: <Zap className="w-6 h-6" />,
      titleKey: "features.instantSetup.title",
      descKey: "features.instantSetup.description"
    },
    {
      icon: <Bot className="w-6 h-6" />,
      titleKey: "features.aiGovernors.title",
      descKey: "features.aiGovernors.description"
    }
  ]

  
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-primary-50 text-slate-900">
      <SiteNav active="home" />

      {/* Hero Section */}
      <section className="relative min-h-screen flex items-center justify-center overflow-hidden pt-16">
        {/* Background image */}
        <div className="absolute inset-0">
          <img 
            src="/hero-bg.jpg" 
            alt="Background" 
            className="w-full h-full object-cover opacity-60"
          />
          <div className="absolute inset-0 bg-gradient-to-br from-slate-50/60 via-white/50 to-primary-50/60"></div>
        </div>
        
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center pt-12 sm:pt-0">
            <div className="bg-white/90 backdrop-blur-sm rounded-3xl shadow-xl p-6 sm:p-12 max-w-5xl mx-auto text-left">
            <div className="mb-8 flex justify-center">
              <img src="/logo_horizontal.svg" alt="Realms" className="h-20 sm:h-24 lg:h-28" />
            </div>
            
            <h1 className="text-lg sm:text-xl lg:text-2xl font-bold mb-6 text-center">
              <span className="text-slate-800">{t('hero.title')}</span>
            </h1>
            
            <div className="text-base sm:text-lg text-slate-600 mb-8 space-y-2">
              <p className="break-words">{t('hero.subtitle1')}</p>
              <p className="break-words">{t('hero.subtitle2')}</p>
              <p className="break-words">{t('hero.subtitle3')}</p>
            </div>
            
            <div className="flex flex-col items-center gap-3">
              <div className="flex flex-col sm:flex-row justify-center gap-3">
                <a href="https://demo.gos.earth" target="_blank" rel="noopener noreferrer"
                   className="inline-flex items-center justify-center px-8 py-4 bg-primary-900 text-white rounded-xl font-semibold text-lg hover:bg-primary-800 transition-colors">
                  {t('hero.tryDemo')}
                </a>
                <a href="https://tally.so/r/GxQ8QL" target="_blank" rel="noopener noreferrer"
                   className="inline-flex items-center justify-center px-8 py-4 border-2 border-primary-900 text-primary-900 bg-white/80 rounded-xl font-semibold text-lg hover:bg-primary-50 transition-colors">
                  {t('hero.requestInvite')}
                  <ArrowRight className="w-5 h-5 ml-2" />
                </a>
              </div>
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-200">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>
                {t('hero.alphaBadge')}
              </span>
            </div>
          </div>
        </div>

        {/* Scroll indicator */}
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 animate-bounce">
          <ChevronRight className="w-6 h-6 rotate-90 text-slate-500" />
        </div>
      </section>

      {/* Mission Section */}
      <section className="py-24 bg-primary-50">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <h2 className="text-3xl sm:text-4xl font-bold mb-8 text-slate-800">
            {t('mission.title')}
          </h2>
          <p className="text-base text-slate-600 leading-relaxed text-justify" dangerouslySetInnerHTML={{ __html: t('mission.description') }} />
          {/* Temporarily hidden — paper website under construction
          <a 
            href={t('mission.learnMoreUrl')}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 mt-6 px-5 py-2.5 bg-primary-50 border border-primary-200 text-primary-700 rounded-lg font-medium hover:bg-primary-100 hover:border-primary-300 transition-all"
          >
            {t('mission.learnMore')}
            <ExternalLink className="w-4 h-4" />
          </a>
          */}
        </div>
      </section>

      {/* Design Principles Section */}
      <section className="py-24 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <h2 className="text-3xl sm:text-4xl font-bold mb-4 text-center text-slate-800">
            {t('principles.title')}
          </h2>
          <p className="text-base text-slate-600 text-center mb-16 max-w-3xl mx-auto">
            {t('principles.intro')}
          </p>
          
          <div className="grid md:grid-cols-2 gap-8">
            <div className="bg-primary-50 rounded-2xl p-8">
              <div className="text-primary-500 font-semibold mb-2">01</div>
              <h3 className="text-lg sm:text-xl font-bold mb-4 text-slate-800">{t('principles.transparency.title')}</h3>
              <p className="text-base text-slate-600">
                {t('principles.transparency.description')}
              </p>
            </div>
            
            <div className="bg-primary-50 rounded-2xl p-8">
              <div className="text-primary-500 font-semibold mb-2">02</div>
              <h3 className="text-lg sm:text-xl font-bold mb-4 text-slate-800">{t('principles.efficiency.title')}</h3>
              <p className="text-base text-slate-600">
                {t('principles.efficiency.description')}
              </p>
            </div>
            
            <div className="bg-primary-50 rounded-2xl p-8">
              <div className="text-primary-500 font-semibold mb-2">03</div>
              <h3 className="text-lg sm:text-xl font-bold mb-4 text-slate-800">{t('principles.diversity.title')}</h3>
              <p className="text-base text-slate-600">
                {t('principles.diversity.description')}
              </p>
            </div>
            
            <div className="bg-primary-50 rounded-2xl p-8">
              <div className="text-primary-500 font-semibold mb-2">04</div>
              <h3 className="text-lg sm:text-xl font-bold mb-4 text-slate-800">{t('principles.resilience.title')}</h3>
              <p className="text-base text-slate-600">
                {t('principles.resilience.description')}
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section - How It Works */}
      <section id="features" className="py-24 relative">
        <div className="absolute inset-0 bg-dots opacity-30"></div>
        <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold mb-4 text-slate-800">
              {t('features.title')}
            </h2>
            <p className="text-base text-slate-600 max-w-2xl mx-auto">
              {t('features.subtitle')}
            </p>
          </div>

          <div className="space-y-24">
            {features.map((feature, index) => (
              <div 
                key={index}
                className={`flex flex-col ${index % 2 === 0 ? 'lg:flex-row' : 'lg:flex-row-reverse'} gap-12 items-center`}
              >
                {/* Image Side - only show if feature has an image */}
                {feature.image && (
                  <div className="flex-1 w-full">
                    <button 
                      onClick={() => setLightboxImage(feature.image)}
                      className="rounded-2xl shadow-lg overflow-hidden border border-slate-200 cursor-zoom-in hover:shadow-2xl hover:scale-[1.02] hover:border-primary-300 transition-all duration-300 w-full group"
                    >
                      <img src={feature.image} alt={t(feature.titleKey)} className="w-full h-auto group-hover:brightness-105 transition-all duration-300" />
                    </button>
                  </div>
                )}
                {/* Content Side */}
                <div className="flex-1 w-full">
                  <div className="flex items-center gap-4 mb-4">
                    <div className="w-12 h-12 rounded-xl bg-primary-100 flex items-center justify-center text-primary-700">
                      {feature.icon}
                    </div>
                    <h3 className="text-2xl font-bold text-slate-800">{t(feature.titleKey)}</h3>
                  </div>
                  <p className="text-lg text-slate-600 leading-relaxed">{t(feature.descKey)}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* For People Section */}
      <section id="forpeople" className="py-24 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold mb-4 text-slate-800">
              {t('forPeople.title')}
            </h2>
            <p className="text-base text-slate-600 max-w-3xl mx-auto">
              {t('forPeople.subtitle')}
            </p>
          </div>

          <div className="grid lg:grid-cols-3 gap-8">
            {/* Identity Verification Card */}
            <div className="glass-dark rounded-2xl p-8">
              <div className="w-14 h-14 rounded-xl bg-primary-100 flex items-center justify-center text-primary-700 mb-6">
                <Fingerprint className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-semibold text-slate-800 mb-4">{t('forPeople.identity.title')}</h3>
              <p className="text-base text-slate-600 mb-6">{t('forPeople.identity.description')}</p>
              <ul className="space-y-2">
                {(t('forPeople.identity.features', { returnObjects: true }) || []).map((feature, idx) => (
                  <li key={idx} className="flex items-start gap-2 text-sm text-slate-600">
                    <CheckCircle className="w-4 h-4 text-green-500 mt-0.5 flex-shrink-0" />
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
            </div>

            {/* Participate Card */}
            <div className="glass-dark rounded-2xl p-8">
              <div className="w-14 h-14 rounded-xl bg-primary-100 flex items-center justify-center text-primary-700 mb-6">
                <Vote className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-semibold text-slate-800 mb-4">{t('forPeople.participate.title')}</h3>
              <p className="text-base text-slate-600">{t('forPeople.participate.description')}</p>
            </div>

            {/* Create Your Own Realm Card */}
            <div className="glass-dark rounded-2xl p-8">
              <div className="w-14 h-14 rounded-xl bg-primary-100 flex items-center justify-center text-primary-700 mb-6">
                <UserPlus className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-semibold text-slate-800 mb-4">{t('forPeople.create.title')}</h3>
              <p className="text-base text-slate-600">{t('forPeople.create.description')}</p>
            </div>
          </div>
        </div>
      </section>

      {/* For Institutions Section */}
      <section id="forinstitutions" className="py-24 bg-primary-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold mb-4 text-slate-800">
              {t('forInstitutions.title')}
            </h2>
            <p className="text-base text-slate-600 max-w-3xl mx-auto">
              {t('forInstitutions.subtitle')}
            </p>
          </div>

          <div className="max-w-5xl mx-auto mb-12">
            {/* For Governments Card */}
            <div className="glass-dark rounded-2xl p-8 lg:p-12">
              <div className="flex flex-col lg:flex-row gap-8 lg:gap-12">
                {/* Left side - Title and description */}
                <div className="flex-1">
                  <div className="w-14 h-14 rounded-xl bg-primary-100 flex items-center justify-center text-primary-700 mb-6">
                    <Landmark className="w-7 h-7" />
                  </div>
                  <h3 className="text-2xl font-semibold text-slate-800 mb-4">{t('forInstitutions.governments.title')}</h3>
                  <p className="text-lg text-slate-600">{t('forInstitutions.governments.description')}</p>
                </div>
                {/* Right side - Benefits */}
                <div className="flex-1 lg:border-l lg:border-slate-200 lg:pl-12">
                  <h4 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-4">Key Benefits</h4>
                  <ul className="space-y-3">
                    {(t('forInstitutions.governments.benefits', { returnObjects: true }) || []).map((benefit, idx) => (
                      <li key={idx} className="flex items-start gap-3 text-base text-slate-600">
                        <CheckCircle className="w-5 h-5 text-green-500 mt-0.5 flex-shrink-0" />
                        <span>{benefit}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </div>

          <div className="text-center">
            <a 
              href="mailto:contact@realmsgos.org"
              className="inline-flex items-center justify-center px-8 py-4 bg-primary-900 text-white rounded-xl font-semibold text-lg hover:bg-primary-800 transition-colors"
            >
              {t('forInstitutions.cta')}
              <Mail className="w-5 h-5 ml-2" />
            </a>
          </div>
        </div>
      </section>

      {/* Get Started Section */}
      <section id="getstarted" className="py-24 relative overflow-hidden">
        {/* Background image */}
        <div className="absolute inset-0">
          <img 
            src="/hero2-bg.jpg" 
            alt="Background" 
            className="w-full h-full object-cover opacity-50"
          />
          <div className="absolute inset-0 bg-gradient-to-br from-slate-50/70 via-white/60 to-primary-50/70"></div>
        </div>
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold mb-4 text-slate-800">
              <span className="text-gradient">{t('getStarted.title')}</span>
            </h2>
            <p className="text-base text-slate-600 max-w-2xl mx-auto">
              {t('getStarted.subtitle')}
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-8 max-w-5xl mx-auto">
            {/* Try Demo Card */}
            <div className="glass-dark rounded-2xl p-8 flex flex-col text-center">
              <div className="w-14 h-14 rounded-xl bg-primary-100 flex items-center justify-center text-primary-700 mb-6 mx-auto">
                <Zap className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-semibold text-slate-800 mb-3">{t('getStarted.demo.title')}</h3>
              <p className="text-base text-slate-600 mb-6 flex-grow">{t('getStarted.demo.description')}</p>
              <a 
                href="https://demo.gos.earth" 
                target="_blank" 
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center px-6 py-3 bg-primary-900 text-white rounded-xl font-semibold hover:bg-primary-800 transition-colors"
              >
                {t('getStarted.demo.button')}
                <ArrowRight className="w-4 h-4 ml-2" />
              </a>
            </div>

            {/* Contact Card */}
            <div className="glass-dark rounded-2xl p-8 flex flex-col text-center">
              <div className="w-14 h-14 rounded-xl bg-primary-100 flex items-center justify-center text-primary-700 mb-6 mx-auto">
                <Users className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-semibold text-slate-800 mb-3">{t('getStarted.contact.title')}</h3>
              <p className="text-base text-slate-600 mb-6 flex-grow">{t('getStarted.contact.description')}</p>
              <a 
                href="mailto:contact@realmsgos.org"
                className="inline-flex items-center justify-center px-6 py-3 border-2 border-primary-900 text-primary-900 rounded-xl font-semibold hover:bg-primary-50 transition-colors"
              >
                {t('getStarted.contact.button')}
                <Mail className="w-4 h-4 ml-2" />
              </a>
            </div>

            {/* Developers Card */}
            <div className="glass-dark rounded-2xl p-8 flex flex-col text-center">
              <div className="w-14 h-14 rounded-xl bg-primary-100 flex items-center justify-center text-primary-700 mb-6 mx-auto">
                <Code className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-semibold text-slate-800 mb-3">{t('getStarted.developers.title')}</h3>
              <p className="text-base text-slate-600 mb-6 flex-grow">{t('getStarted.developers.description')}</p>
              <a 
                href="https://github.com/smart-social-contracts/realms-gos"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center px-6 py-3 border-2 border-primary-900 text-primary-900 rounded-xl font-semibold hover:bg-primary-50 transition-colors"
              >
                {t('getStarted.developers.button')}
                <BookOpen className="w-4 h-4 ml-2" />
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 border-t border-slate-200 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col md:flex-row items-center justify-between gap-6">
            <div className="flex items-center gap-3">
              <img src="/logo_horizontal.svg" alt="Realms GOS" className="h-12" />
            </div>
            
            <div className="flex items-center gap-6">
              <a href="https://github.com/smart-social-contracts/realms-gos" target="_blank" rel="noopener noreferrer"
                 className="text-slate-500 hover:text-slate-900 transition-colors">
                <Github className="w-6 h-6" />
              </a>
              <a href="https://x.com/realmsgos" target="_blank" rel="noopener noreferrer"
                 className="text-slate-500 hover:text-slate-900 transition-colors">
                <svg className="w-6 h-6" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
                </svg>
              </a>
              <a href="mailto:contact@realmsgos.org"
                 className="text-slate-500 hover:text-slate-900 transition-colors">
                <Mail className="w-6 h-6" />
              </a>
              <a href="https://github.com/smart-social-contracts/realms-gos/tree/main/docs"
                 target="_blank"
                 rel="noopener noreferrer"
                 className="text-slate-500 hover:text-slate-900 transition-colors">
                <BookOpen className="w-6 h-6" />
              </a>
            </div>
          </div>
          
          <div className="mt-8 pt-8 border-t border-slate-200 text-center text-slate-500 text-sm">
            <a href="https://internetcomputer.org" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-slate-700 transition-colors">
              {t('footer.builtOn')} <img src="/images/internet-computer-icp-logo.svg" alt="Internet Computer" className="inline h-4 w-4 mx-1" /> {t('footer.withLove')}
            </a>
            <div className="mt-2">{t('footer.openSource')}</div>
            <div className="mt-2 text-xs text-slate-400">
              Realms GOS {typeof __BUILD_VERSION__ !== 'undefined' ? __BUILD_VERSION__ : 'dev'} ({typeof __BUILD_COMMIT__ !== 'undefined' ? __BUILD_COMMIT__ : 'local'}) - {typeof __BUILD_TIME__ !== 'undefined' ? __BUILD_TIME__ : new Date().toISOString().replace('T', ' ').substring(0, 19)}{window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' ? ' - Local deployment' : ''}
            </div>
          </div>
        </div>
      </footer>

      {/* Lightbox Modal */}
      {lightboxImage && (
        <div 
          className="fixed inset-0 z-[100] bg-black/90 flex items-center justify-center p-4 cursor-zoom-out"
          onClick={() => setLightboxImage(null)}
        >
          <button 
            className="absolute top-4 right-4 text-white hover:text-slate-300 transition-colors"
            onClick={() => setLightboxImage(null)}
          >
            <X className="w-8 h-8" />
          </button>
          <img 
            src={lightboxImage} 
            alt="Zoomed view" 
            className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  )
}

export default LandingPage
