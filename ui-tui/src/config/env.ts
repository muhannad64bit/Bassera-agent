export const STARTUP_RESUME_ID = (process.env.BASSERA_TUI_RESUME ?? '').trim()
export const MOUSE_TRACKING = !/^(?:1|true|yes|on)$/i.test((process.env.BASSERA_TUI_DISABLE_MOUSE ?? '').trim())
export const NO_CONFIRM_DESTRUCTIVE = /^(?:1|true|yes|on)$/i.test((process.env.BASSERA_TUI_NO_CONFIRM ?? '').trim())
