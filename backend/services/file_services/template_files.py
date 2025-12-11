import base64
from pathlib import Path
from config.paths import get_templates_path


class TemplateFileService:
    """模板文件管理服务"""
    
    def __init__(self):
        self.base_path = get_templates_path()
    
    def save_file(self, template_id: int, filename: str, content: str, is_binary: bool = False) -> str:
        """保存模板文件，返回文件名"""
        file_name = f"{template_id}_{filename}"
        file_path = self.base_path / file_name
        
        # 确保目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        if is_binary:
            # 解码base64并写入二进制文件
            binary_content = base64.b64decode(content)
            with open(file_path, 'wb') as f:
                f.write(binary_content)
        else:
            # 写入文本文件
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
        
        return file_name
    
    def get_text_content(self, filename: str) -> str:
        """获取文本文件内容（用于创建工作空间时）"""
        file_path = self.base_path / filename
        if not file_path.exists():
            raise FileNotFoundError(f"Template file not found: {filename}")
        return file_path.read_text(encoding='utf-8')
    
    def delete_file(self, filename: str) -> bool:
        """删除模板文件"""
        file_path = self.base_path / filename
        if file_path.exists():
            file_path.unlink()
        return True


# 创建全局实例
template_file_service = TemplateFileService()
