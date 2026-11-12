export interface ToFixedViolation {
  file: string
  line: number
  source: string
}

export function scanText(filePath: string, text: string): ToFixedViolation[]
export function scanRepo(root: string): ToFixedViolation[]
