export function isImageFile(path: string): boolean {
  return /\.(jpe?g|png|gif|bmp|svg|webp)$/i.test(path)
}
