import brand from '../../../../src/brand.json'

export const BRAND_NAME_EN: string = brand.name_en
export const BRAND_NAME_ZH: string = brand.name_zh
export const BRAND_LOCKUP = `${BRAND_NAME_EN} · ${BRAND_NAME_ZH}`
export const BRAND_CATEGORY: string = brand.category

/**
 * 工作台显示名（可改名配置的默认值，控制台唯一事实源之一）。
 *
 * 2026-11-12 产品改名：产品在用户可见处叫贾维斯 / Jarvis；Muse 本人的
 * 名字仍是 Muse，不要混淆。名字可改：向导与设置页能改工作台名字，存
 * 配置里，全站只从这一个配置读。
 *
 * 当前值是 Joseph 这份的默认名字（贾维斯 / Jarvis），与 `src/brand.json`
 * 的出厂默认值一致。运行时名字由 `lib/workspaceName.ts` 的 store 读
 * `GET /v1/workspace/name`（G 组建的）；它拉不到/没改名时就用这里的
 * 默认值——所以这两个常量是 store 的回落值，不是第二套事实源。
 * `BRAND_*`（`src/brand.json`）不要动：它是出厂默认值的来源。
 */
export const WORKBENCH_NAME_ZH = '贾维斯'
export const WORKBENCH_NAME_EN = 'Jarvis'
export const WORKBENCH_LOCKUP = `${WORKBENCH_NAME_EN} · ${WORKBENCH_NAME_ZH}`
