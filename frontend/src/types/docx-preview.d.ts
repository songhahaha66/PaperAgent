declare module 'docx-preview' {
  export interface RenderOptions {
    className?: string
    inWrapper?: boolean
    ignoreWidth?: boolean
    ignoreHeight?: boolean
    ignoreFonts?: boolean
    breakPages?: boolean
    ignoreLastRenderedPageBreak?: boolean
    experimental?: boolean
    trimXmlDeclaration?: boolean
    useBase64URL?: boolean
    useMathMLPolyfill?: boolean
    renderChanges?: boolean
    renderHeaders?: boolean
    renderFooters?: boolean
    renderFootnotes?: boolean
    renderEndnotes?: boolean
  }

  export function renderAsync(
    data: Blob | ArrayBuffer | Uint8Array,
    bodyContainer: HTMLElement,
    styleContainer?: HTMLElement | null,
    options?: RenderOptions
  ): Promise<void>
}
