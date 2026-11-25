import { useState, useEffect, useCallback } from 'react'
import { AuthClient } from '@icp-sdk/auth/client'

// The session lengths @dfinity/auth-client applied by default, kept so the move
// to @icp-sdk/auth (whose defaults are 30 days and 7 days) changes neither.
const SESSION_TTL_NS = BigInt(8 * 60 * 60 * 1_000_000_000) // 8 hours
const SESSION_IDLE_NS = BigInt(10 * 60 * 1_000_000_000) // 10 minutes

let authClient = null

function getClient() {
  authClient ??= new AuthClient()
  return authClient
}

/** The principal this tab can act as, or null: a stored record alone is not a sign-in. */
async function heldPrincipal() {
  try {
    const principal = (await getClient().getIdentity()).getPrincipal()
    return principal.isAnonymous() ? null : principal.toText()
  } catch (e) {
    console.warn('Stored session is not usable:', e)
    return null
  }
}

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [principal, setPrincipal] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    let mounted = true
    async function check() {
      const held = await heldPrincipal()
      if (mounted) {
        setIsAuthenticated(held !== null)
        setPrincipal(held)
        setIsLoading(false)
      }
    }
    check()
    return () => { mounted = false }
  }, [])

  const login = useCallback(async () => {
    setIsLoading(true)
    try {
      const identity = await getClient().signIn({ maxTimeToLive: SESSION_TTL_NS, maxTimeToIdle: SESSION_IDLE_NS })
      setIsAuthenticated(true)
      setPrincipal(identity.getPrincipal().toText())
    } catch (e) {
      console.error('Login failed:', e)
    } finally {
      setIsLoading(false)
    }
  }, [])

  const logout = useCallback(async () => {
    try {
      // Ends the session at Internet Identity, not only in this browser.
      await getClient().signOut()
    } catch (e) {
      // This browser is signed out either way; the session at II lasts until it expires.
      console.error('Internet Identity did not end the session:', e)
    }
    setIsAuthenticated(false)
    setPrincipal(null)
  }, [])

  return { isAuthenticated, principal, isLoading, login, logout }
}
