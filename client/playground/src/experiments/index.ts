import type { ComponentType } from 'react'
import Announcer from './Announcer'
import PushToTalk from './PushToTalk'
import GlossyCursor from './GlossyCursor'
import LiquidGlassCursor from './LiquidGlassCursor'
import CausticsCursor from './CausticsCursor'
import RefractionCursor from './RefractionCursor'
import DropletBench from './DropletBench'
import MobileGlass from './MobileGlass'
import RainHero from './RainHero'
import BubbleMockups from './BubbleMockups'
import ArtemisLiquidUI from './ArtemisLiquidUI'

// Add an experiment: create a component in this folder and list it here.
export type Experiment = { id: string; title: string; Component: ComponentType }

export const experiments: Experiment[] = [
  { id: 'artemis-liquid', title: 'Artemis UI · liquid agent', Component: ArtemisLiquidUI },
  { id: 'bubble-mockups', title: 'Agent bubble mockups', Component: BubbleMockups },
  { id: 'rain-hero', title: 'Rain hero', Component: RainHero },
  { id: 'mobile-glass', title: 'Mobile glass UI', Component: MobileGlass },
  { id: 'droplet-bench', title: 'Droplet test bench', Component: DropletBench },
  { id: 'refraction', title: 'Refraction cursor', Component: RefractionCursor },
  { id: 'caustics', title: 'Caustics cursor', Component: CausticsCursor },
  { id: 'liquid-glass', title: 'Liquid glass cursor', Component: LiquidGlassCursor },
  { id: 'glossy-cursor', title: 'Glossy cursor', Component: GlossyCursor },
  { id: 'announcer', title: 'Live-region announcer', Component: Announcer },
  { id: 'push-to-talk', title: 'Push-to-talk states', Component: PushToTalk },
]
