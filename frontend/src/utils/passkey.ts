import {
  browserSupportsWebAuthn,
  browserSupportsWebAuthnAutofill,
  startAuthentication,
  startRegistration,
  WebAuthnAbortService,
} from '@simplewebauthn/browser'
import type {
  AuthenticationResponseJSON,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  RegistrationResponseJSON,
} from '@simplewebauthn/browser'

export function isPasskeySupported(): boolean {
  return browserSupportsWebAuthn()
}

export function isPasskeyOriginSupported(): boolean {
  if (typeof window === 'undefined') return false
  const host = window.location.hostname
  if (host === 'localhost') return true
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) return false
  return window.isSecureContext
}

export function passkeyOriginHint(): string | null {
  if (isPasskeyOriginSupported()) return null
  const host = window.location.hostname
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
    return 'Passkey 不能使用 IP 访问，请改用 localhost 或 HTTPS 域名'
  }
  return 'Passkey 需要安全上下文（HTTPS 或 localhost）'
}

export function isPasskeyAutofillSupported(): Promise<boolean> {
  return browserSupportsWebAuthnAutofill()
}

export function cancelPasskeyCeremony(): void {
  WebAuthnAbortService.cancelCeremony()
}

export async function createPasskey(
  options: PublicKeyCredentialCreationOptionsJSON,
): Promise<RegistrationResponseJSON> {
  return startRegistration({ optionsJSON: options })
}

export async function assertPasskey(
  options: PublicKeyCredentialRequestOptionsJSON,
  useBrowserAutofill = false,
): Promise<AuthenticationResponseJSON> {
  return startAuthentication({
    optionsJSON: options,
    useBrowserAutofill,
  })
}

export function isPasskeyCanceled(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const err = error as { name?: string; code?: string }
  return (
    err.name === 'NotAllowedError' ||
    err.name === 'AbortError' ||
    err.code === 'ERROR_CEREMONY_ABORTED'
  )
}
