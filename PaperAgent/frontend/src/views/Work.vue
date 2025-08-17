const renderMarkdown = (text: string) => {
  // 先处理代码块，避免代码块中的标记被处理
  let codeBlocks: string[] = [];
  let codeBlockCounter = 0;
  
  // 提取代码块（包括语言标识）
  text = text.replace(/```(\w+)?\n([\s\S]*?)\n```/g, (match, lang, content) => {
    codeBlocks.push(`<pre><code class="language-${lang || 'plaintext'}">${content}</code></pre>`);
    return `{{CODE_BLOCK_${codeBlockCounter++}}}`;
  });

  // 处理行内代码
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  
  // 按照从h6到h1的顺序处理标题
  text = text
    .replace(/^###### (.*)$/gm, '<h6>$1</h6>')
    .replace(/^##### (.*)$/gm, '<h5>$1</h5>')
    .replace(/^#### (.*)$/gm, '<h4>$1</h4>')
    .replace(/^### (.*)$/gm, '<h3>$1</h3>')
    .replace(/^## (.*)$/gm, '<h2>$1</h2>')
    .replace(/^# (.*)$/gm, '<h1>$1</h1>');
  
  // 处理粗体和斜体
  text = text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>');
  
  // 处理链接
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  
  // 处理无序列表和有序列表
  text = text.replace(/(^( *)[\*|\-|\+|\d+\.)] .*$)/gm, (match, item, indent) => {
    const listType = match.startsWith(' ') ? 'ul' : 'ol';
    const listItem = `<li>${item.trim()}</li>`;
    return `${indent}<${listType}>${listItem}</${listType}>`;
  });
  
  // 处理换行（但不在块级元素内部添加<br>）
  text = text.replace(/(?<!<br>|<\/p>|<\/div>|<\/li>|<\/ul>|<\/ol>)\n/g, '<br>');

  // 恢复代码块
  for (let i = 0; i < codeBlockCounter; i++) {
    text = text.replace(`{{CODE_BLOCK_${i}}}`, codeBlocks[i]);
  }
  
  return text;
}
