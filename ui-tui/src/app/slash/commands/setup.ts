import { withInkSuspended } from '@bassera/ink'

import { launchBasseraCommand } from '../../../lib/externalCli.js'
import { runExternalSetup } from '../../setupHandoff.js'
import type { SlashCommand } from '../types.js'

export const setupCommands: SlashCommand[] = [
  {
    help: 'configure LLM provider + model (launches `bassera model`)',
    name: 'provider',
    run: (_arg, ctx) =>
      void runExternalSetup({
        args: ['model'],
        ctx,
        done: 'provider updated — starting session…',
        launcher: launchBasseraCommand,
        suspend: withInkSuspended
      })
  },
  {
    help: 'run full setup wizard (launches `bassera setup`)',
    name: 'setup',
    run: (arg, ctx) =>
      void runExternalSetup({
        args: ['setup', ...arg.split(/\s+/).filter(Boolean)],
        ctx,
        done: 'setup complete — starting session…',
        launcher: launchBasseraCommand,
        suspend: withInkSuspended
      })
  }
]
