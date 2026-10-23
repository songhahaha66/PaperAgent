const { vue } = require('./moduleHarness.cjs')
function hostRenderer() {
  const root = { kind: 'root', children: [] }
  function detach(node) {
    if (node.parent) {
      const i = node.parent.children.indexOf(node)
      if (i >= 0) node.parent.children.splice(i, 1)
      node.parent = null
    }
  }
  function walk(node, predicate) {
    if (predicate(node)) return node
    for (const child of node.children || []) {
      const found = walk(child, predicate)
      if (found) return found
    }
  }
  const renderer = vue.createRenderer({
    createElement: (tag) => ({ kind: tag, props: {}, children: [], style: {} }),
    createText: (text) => ({ kind: 'text', text, children: [] }),
    createComment: (text) => ({ kind: 'comment', text, children: [] }),
    setText: (node, text) => {
      node.text = text
    },
    setElementText: (node, text) => {
      node.children = []
      node.text = text
    },
    patchProp: (node, key, previous, value) => {
      node.props[key] = value
    },
    insert(node, parent, anchor) {
      detach(node)
      const index = anchor ? parent.children.indexOf(anchor) : -1
      parent.children.splice(index < 0 ? parent.children.length : index, 0, node)
      node.parent = parent
    },
    remove: detach,
    parentNode: (node) => node.parent,
    nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
    querySelector: (selector) => walk(root, (node) => node.props?.id === selector.slice(1)),
  })
  return { renderer, root, find: (predicate) => walk(root, predicate) }
}
module.exports = { hostRenderer }
