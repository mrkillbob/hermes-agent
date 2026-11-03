import type { Page } from '@playwright/test'

/** Preserve transient form ownership, without recording prompt/answer text. */
export async function captureClarifyOwners(page: Page) {
  return page.evaluateHandle(() => {
    const samples: object[] = []
    let previous = ''

    const capture = () => {
      const forms = [...document.querySelectorAll('form[data-clarify-batch]')]

      const state = {
        total: forms.length,
        forms: forms.slice(0, 16).map(form => ({
          session: form.getAttribute('data-clarify-session-id'),
          message: form.getAttribute('data-clarify-message-id'),
          call: form.getAttribute('data-clarify-tool-call-id'),
          binding: form.getAttribute('data-clarify-bound-request-id'),
          request: form.getAttribute('data-clarify-request-id'),
          layoutVisible: form.getClientRects().length > 0,
          choices: form.querySelectorAll('button[data-choice]').length,
          enabledChoices: form.querySelectorAll('button[data-choice]:not([disabled])').length
        }))
      }

      const serialized = JSON.stringify(state)

      if (serialized !== previous) {
        previous = serialized
        samples.push({ at: performance.now(), ...state })

        if (samples.length > 64) {samples.shift()}
      }
    }

    const observer = new MutationObserver(capture)
    observer.observe(document, { childList: true, subtree: true, attributes: true,
      attributeFilter: ['disabled', 'hidden', 'class', 'style', 'data-clarify-session-id',
        'data-clarify-message-id', 'data-clarify-tool-call-id', 'data-clarify-bound-request-id', 'data-clarify-request-id'] })
    capture()

    return { samples, stop: () => observer.disconnect() }
  })
}
