export type ScenarioIconName = 'Monitor' | 'List' | 'DataLine' | 'Share'

export interface ScenarioItem {
  label: string
  query: string
}

export interface ScenarioGroup {
  id: string
  title: string
  icon: ScenarioIconName
  items: ScenarioItem[]
}

export const scenarioGroups: ScenarioGroup[]
