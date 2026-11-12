/**
 * 极简二维码编码器：字节模式 / 纠错等级 L / 版本 1–5 / 固定掩码 0。
 *
 * 只服务于「手机扫一下配对链接」这一件事，所以刻意不引 npm 依赖（少一个包就
 * 少一份供应链风险）。配对链接约 50 字节，版本 1–5 的容量（最多 108 字节）
 * 足够；超出直接抛错，而不是悄悄编码出一个扫不出来的码。
 *
 * 没实现的部分（用不到，也不打算用）：数字/字母数字模式、纠错等级 M/Q/H、
 * 版本 6 以上（含版本信息区）、多 RS 块交织、掩码择优。
 */

type Spec = {
  version: number
  size: number
  dataCodewords: number
  ecCodewords: number
}

/** 纠错等级 L、单 RS 块的版本参数（标准表）。 */
const SPECS: Spec[] = [
  { version: 1, size: 21, dataCodewords: 19, ecCodewords: 7 },
  { version: 2, size: 25, dataCodewords: 34, ecCodewords: 10 },
  { version: 3, size: 29, dataCodewords: 55, ecCodewords: 15 },
  { version: 4, size: 33, dataCodewords: 80, ecCodewords: 20 },
  { version: 5, size: 37, dataCodewords: 108, ecCodewords: 26 },
]

const EXP = new Uint8Array(512)
const LOG = new Uint8Array(256)
{
  let x = 1
  for (let i = 0; i < 255; i += 1) {
    EXP[i] = x
    LOG[x] = i
    x <<= 1
    if (x & 0x100) x ^= 0x11d
  }
  for (let i = 255; i < 512; i += 1) EXP[i] = EXP[i - 255]
}

function mul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0
  return EXP[LOG[a] + LOG[b]]
}

function generator(degree: number): number[] {
  let poly = [1]
  for (let i = 0; i < degree; i += 1) {
    const next = new Array<number>(poly.length + 1).fill(0)
    for (let j = 0; j < poly.length; j += 1) {
      next[j] ^= poly[j]
      next[j + 1] ^= mul(poly[j], EXP[i])
    }
    poly = next
  }
  return poly
}

function reedSolomon(data: number[], ecLength: number): number[] {
  const gen = generator(ecLength)
  const remainder = new Array<number>(ecLength).fill(0)
  for (const byte of data) {
    const factor = byte ^ remainder[0]
    remainder.shift()
    remainder.push(0)
    if (factor !== 0) {
      for (let i = 0; i < ecLength; i += 1) remainder[i] ^= mul(gen[i + 1], factor)
    }
  }
  return remainder
}

class BitBuffer {
  readonly bits: number[] = []

  push(value: number, length: number): void {
    for (let i = length - 1; i >= 0; i -= 1) this.bits.push((value >>> i) & 1)
  }

  get length(): number {
    return this.bits.length
  }
}

function buildCodewords(text: string, spec: Spec): number[] {
  const bytes = Array.from(new TextEncoder().encode(text))
  const buffer = new BitBuffer()
  buffer.push(0b0100, 4) // 字节模式
  buffer.push(bytes.length, 8) // 版本 1–9 的字符计数是 8 位
  for (const byte of bytes) buffer.push(byte, 8)

  const capacityBits = spec.dataCodewords * 8
  if (buffer.length > capacityBits) throw new Error('二维码内容过长')
  const terminator = Math.min(4, capacityBits - buffer.length)
  buffer.push(0, terminator)
  while (buffer.length % 8 !== 0) buffer.push(0, 1)

  const data: number[] = []
  for (let i = 0; i < buffer.bits.length; i += 8) {
    let value = 0
    for (let j = 0; j < 8; j += 1) value = (value << 1) | buffer.bits[i + j]
    data.push(value)
  }
  const pads = [0xec, 0x11]
  let padIndex = 0
  while (data.length < spec.dataCodewords) {
    data.push(pads[padIndex % 2])
    padIndex += 1
  }
  return [...data, ...reedSolomon(data, spec.ecCodewords)]
}

function pickSpec(byteLength: number): Spec {
  const needed = 4 + 8 + byteLength * 8
  const spec = SPECS.find((item) => needed <= item.dataCodewords * 8)
  if (!spec) throw new Error('二维码内容过长（超过版本 5 的容量）')
  return spec
}

/** 纠错等级 L(=01) + 掩码 0 的 15 位格式信息。 */
function formatBits(): number {
  const data = (0b01 << 3) | 0 // L + mask 0
  let remainder = data
  for (let i = 0; i < 10; i += 1) {
    remainder = (remainder << 1) ^ ((remainder >>> 9) * 0x537)
  }
  return ((data << 10) | remainder) ^ 0x5412
}

const ALIGNMENT: Record<number, number[]> = {
  2: [6, 18],
  3: [6, 22],
  4: [6, 26],
  5: [6, 30],
}

/** 生成二维码点阵：`true` 表示黑块。 */
export function qrMatrix(text: string): boolean[][] {
  const spec = pickSpec(new TextEncoder().encode(text).length)
  const { size } = spec
  const modules: boolean[][] = Array.from({ length: size }, () =>
    new Array<boolean>(size).fill(false),
  )
  const reserved: boolean[][] = Array.from({ length: size }, () =>
    new Array<boolean>(size).fill(false),
  )

  const set = (row: number, col: number, dark: boolean): void => {
    if (row < 0 || col < 0 || row >= size || col >= size) return
    modules[row][col] = dark
    reserved[row][col] = true
  }

  const placeFinder = (row: number, col: number): void => {
    for (let r = -1; r <= 7; r += 1) {
      for (let c = -1; c <= 7; c += 1) {
        const rr = row + r
        const cc = col + c
        if (rr < 0 || cc < 0 || rr >= size || cc >= size) continue
        const inCore = r >= 0 && r <= 6 && c >= 0 && c <= 6
        const dark =
          inCore && (r === 0 || r === 6 || c === 0 || c === 6 || (r >= 2 && r <= 4 && c >= 2 && c <= 4))
        set(rr, cc, dark)
      }
    }
  }

  placeFinder(0, 0)
  placeFinder(0, size - 7)
  placeFinder(size - 7, 0)

  // 格式信息区与固定暗块先占位，避免数据位落进去。
  for (let i = 0; i <= 8; i += 1) {
    set(8, i, false)
    set(i, 8, false)
  }
  for (let i = 0; i < 8; i += 1) {
    set(8, size - 1 - i, false)
    set(size - 1 - i, 8, false)
  }
  set(size - 8, 8, true)

  // 定位图形（时序线）
  for (let i = 8; i < size - 8; i += 1) {
    const dark = i % 2 === 0
    set(6, i, dark)
    set(i, 6, dark)
  }

  const centers = ALIGNMENT[spec.version]
  if (centers) {
    for (const row of centers) {
      for (const col of centers) {
        if (reserved[row][col]) continue
        for (let r = -2; r <= 2; r += 1) {
          for (let c = -2; c <= 2; c += 1) {
            const dark = Math.max(Math.abs(r), Math.abs(c)) !== 1
            set(row + r, col + c, dark)
          }
        }
      }
    }
  }

  const codewords = buildCodewords(text, spec)
  let index = 0
  let upward = true
  for (let col = size - 1; col > 0; col -= 2) {
    if (col === 6) col -= 1
    for (let i = 0; i < size; i += 1) {
      const row = upward ? size - 1 - i : i
      for (let offset = 0; offset < 2; offset += 1) {
        const cc = col - offset
        if (reserved[row][cc]) continue
        let dark = false
        if (index < codewords.length * 8) {
          dark = ((codewords[index >> 3] >> (7 - (index & 7))) & 1) === 1
          index += 1
        }
        // 固定掩码 0：(row + col) % 2 === 0 时翻转。
        if ((row + cc) % 2 === 0) dark = !dark
        modules[row][cc] = dark
      }
    }
    upward = !upward
  }

  const format = formatBits()
  for (let i = 0; i < 15; i += 1) {
    const dark = ((format >> i) & 1) === 1
    if (i < 6) modules[i][8] = dark
    else if (i < 8) modules[i + 1][8] = dark
    else modules[size - 15 + i][8] = dark

    if (i < 8) modules[8][size - i - 1] = dark
    else if (i < 9) modules[8][15 - i] = dark
    else modules[8][15 - i - 1] = dark
  }
  // 暗块固定为黑（位置见规格：右下角第 8 列）。
  modules[size - 8][8] = true

  return modules
}
