import { Link, useLocation } from 'react-router-dom'
import { useUser, useClerk } from '@clerk/clerk-react'
import {
  LayoutDashboard,
  FileText,
  Plus,
  Settings,
  LogOut,
  Menu,
  X,
  Zap,
  GitBranch,
  Bell,
  Star,
  QrCode,
  CreditCard,
  Calendar,
  Lock,
  Sparkles,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { APP_NAME } from '@/lib/branding'
import { setLanguage } from '@/lib/i18n'

// Phase 1 — core nav items (fully functional)
const coreNavItems = [
  { icon: LayoutDashboard, label: 'Dashboard', labelEs: 'Panel Principal', path: '/dashboard' },
  { icon: Plus, label: 'New Proposal', labelEs: 'Nueva Propuesta', path: '/proposals/new' },
  { icon: FileText, label: 'Proposals', labelEs: 'Propuestas', path: '/proposals' },
  { icon: Settings, label: 'Settings', labelEs: 'Configuración', path: '/settings' },
]

// Coming Soon — visible but locked
const comingSoonItems = [
  { icon: GitBranch, label: 'Pipeline', labelEs: 'Pipeline' },
  { icon: Bell, label: 'Follow-ups', labelEs: 'Seguimientos' },
  { icon: CreditCard, label: 'Invoices', labelEs: 'Facturas' },
  { icon: Calendar, label: 'Schedule', labelEs: 'Agenda' },
  { icon: Star, label: 'Reviews', labelEs: 'Reseñas' },
  { icon: QrCode, label: 'QR Codes', labelEs: 'Códigos QR' },
  { icon: Sparkles, label: 'AI Coach', labelEs: 'Asesor IA' },
]

export function Sidebar() {
  const location = useLocation()
  const { user } = useUser()
  const { signOut } = useClerk()
  const { i18n } = useTranslation()
  const [mobileOpen, setMobileOpen] = useState(false)
  const isEs = i18n.language === 'es'

  const toggleLang = () => {
    setLanguage(isEs ? 'en' : 'es')
  }

  return (
    <>
      {/* Mobile header */}
      <div className="lg:hidden fixed top-0 left-0 right-0 z-50 bg-navy h-14 flex items-center justify-between px-4">
        <div className="flex items-center gap-2">
          <Zap className="h-6 w-6 text-accent" />
          <span className="text-white font-heading font-bold text-lg">{APP_NAME}</span>
        </div>
        <button
          onClick={() => setMobileOpen(!mobileOpen)}
          className="text-white p-2"
        >
          {mobileOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
        </button>
      </div>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black/50"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed top-0 left-0 z-40 h-full w-64 bg-navy flex flex-col transition-transform duration-200",
          "lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        {/* Logo */}
        <div className="h-16 flex items-center gap-2 px-6 border-b border-white/10">
          <Zap className="h-7 w-7 text-accent" />
          <span className="text-white font-heading font-bold text-xl">{APP_NAME}</span>
        </div>

        {/* Core Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {coreNavItems.map((item) => {
            const isActive = location.pathname === item.path ||
              (item.path !== '/dashboard' && location.pathname.startsWith(item.path))
            return (
              <Link
                key={item.path}
                to={item.path}
                onClick={() => setMobileOpen(false)}
                className={cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                  isActive
                    ? "bg-accent text-white"
                    : "text-white/70 hover:text-white hover:bg-white/10"
                )}
              >
                <item.icon className="h-5 w-5" />
                {isEs ? item.labelEs : item.label}
              </Link>
            )
          })}

          {/* Coming Soon divider */}
          <div className="pt-4 pb-2 px-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-white/30">{isEs ? 'Próximamente' : 'Coming Soon'}</p>
          </div>

          {comingSoonItems.map((item) => (
            <div
              key={item.label}
              className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-white/30 cursor-not-allowed"
            >
              <item.icon className="h-5 w-5" />
              <span className="flex-1">{isEs ? item.labelEs : item.label}</span>
              <Lock className="h-3 w-3" />
            </div>
          ))}
        </nav>

        {/* User section */}
        <div className="p-4 border-t border-white/10">
          <div className="flex items-center gap-3 px-2 mb-3">
            <div className="h-8 w-8 rounded-full bg-accent/20 flex items-center justify-center">
              <span className="text-accent text-sm font-bold">
                {user?.firstName?.[0] || user?.emailAddresses?.[0]?.emailAddress?.[0]?.toUpperCase() || 'C'}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-white text-sm font-medium truncate">
                {user?.fullName || user?.emailAddresses?.[0]?.emailAddress || 'Contractor'}
              </p>
            </div>
          </div>
          {/* Language toggle */}
          <button
            onClick={toggleLang}
            className="flex items-center gap-3 px-3 py-2 w-full rounded-lg text-sm text-white/50 hover:text-white hover:bg-white/10 transition-colors mb-1"
          >
            <span className="text-base">🌐</span>
            {isEs ? 'Switch to English' : 'Cambiar a Español'}
          </button>
          <button
            onClick={() => signOut()}
            className="flex items-center gap-3 px-3 py-2 w-full rounded-lg text-sm text-white/50 hover:text-white hover:bg-white/10 transition-colors"
          >
            <LogOut className="h-4 w-4" />
            {isEs ? 'Cerrar Sesión' : 'Sign Out'}
          </button>
        </div>
      </aside>
    </>
  )
}
