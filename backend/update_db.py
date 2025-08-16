#!/usr/bin/env python3
"""
数据库表结构更新脚本
"""

from models.models import Base
from database.database import engine

def update_database():
    """更新数据库表结构"""
    print("开始更新数据库表结构...")
    
    try:
        # 创建所有表
        Base.metadata.create_all(bind=engine)
        print("数据库表结构更新成功！")
        
        # 显示创建的表
        print("已创建的表:")
        for table_name in Base.metadata.tables.keys():
            print(f"  - {table_name}")
            
    except Exception as e:
        print(f"更新数据库表结构失败: {e}")
        return False
    
    return True

if __name__ == "__main__":
    update_database()
