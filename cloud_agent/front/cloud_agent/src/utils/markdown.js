import { Marked, Renderer } from 'marked'

const SAFE_URL_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:'])
const LOCAL_URL_BASE = 'https://cloud-agent.local'

const escapeHtml = (value) =>
  String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')

const isRelativeUrl = (value) =>
  value.startsWith('#') ||
  (value.startsWith('/') && !value.startsWith('//')) ||
  value.startsWith('./') ||
  value.startsWith('../')

const isSafeUrl = (value) => {
  const href = String(value || '').trim()
  if (!href) return false
  if (isRelativeUrl(href)) return true

  try {
    const url = new URL(href, LOCAL_URL_BASE)
    return SAFE_URL_PROTOCOLS.has(url.protocol)
  } catch {
    return false
  }
}

const renderer = new Renderer()

renderer.html = ({ text }) => escapeHtml(text)

renderer.link = function ({ href, title, tokens }) {
  const label = this.parser.parseInline(tokens)
  if (!isSafeUrl(href)) {
    return label
  }

  const safeTitle = title ? ` title="${escapeHtml(title)}"` : ''
  return `<a href="${escapeHtml(href.trim())}"${safeTitle} target="_blank" rel="noopener noreferrer">${label}</a>`
}

renderer.image = function ({ href, title, text, tokens }) {
  const label = tokens ? this.parser.parseInline(tokens) : escapeHtml(text || '')
  if (!isSafeUrl(href)) {
    return label
  }

  const safeTitle = title ? ` title="${escapeHtml(title)}"` : ''
  return `<img src="${escapeHtml(href.trim())}" alt="${escapeHtml(text || '')}"${safeTitle}>`
}

const marked = new Marked({
  async: false,
  breaks: true,
  gfm: true,
  renderer,
})

export const renderMarkdown = (text) => marked.parse(String(text ?? ''))
