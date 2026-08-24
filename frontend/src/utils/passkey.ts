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
