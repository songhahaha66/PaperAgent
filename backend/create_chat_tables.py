"""
创建聊天相关数据表的迁移脚本
"""

import sys
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models.models import Base

def create_chat_tables():
    """创建聊天相关的数据表"""
    print("🚀 开始创建聊天相关数据表...")
    
    try:
        # 获取数据库连接
        database_url = "postgresql://paperagent:ZWe2Kbm*UwcCJtQiL!@pgm-2zee5i36v4fwm902jo.pg.rds.aliyuncs.com:5432/paperagent"
        print(f"📡 连接到数据库: {database_url.split('@')[1] if '@' in database_url else '本地数据库'}")
        
        engine = create_engine(database_url)
        
        # 创建所有表
        print("🔨 创建数据表...")
        Base.metadata.create_all(bind=engine)
        
        print("✅ 数据表创建成功！")
        
        # 验证表是否创建成功
        with engine.connect() as conn:
            # 检查新创建的表
            tables_to_check = ['chat_sessions', 'chat_messages', 'work_flow_states']
            
            for table_name in tables_to_check:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                count = result.scalar()
                print(f"📊 表 {table_name}: {count} 条记录")
        
        print("\n🎉 所有聊天相关数据表创建完成！")
        return True
        
    except Exception as e:
        print(f"❌ 创建数据表失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def drop_chat_tables():
    """删除聊天相关的数据表（谨慎使用）"""
    print("⚠️  警告：即将删除聊天相关的数据表！")
    confirm = input("确认删除？输入 'yes' 继续: ")
    
    if confirm.lower() != 'yes':
        print("❌ 操作已取消")
        return False
    
    try:
        database_url = get_database_url()
        engine = create_engine(database_url)
        
        # 删除表（注意顺序，先删除有外键依赖的表）
        tables_to_drop = ['chat_messages', 'chat_sessions', 'work_flow_states']
        
        with engine.connect() as conn:
            for table_name in tables_to_drop:
                try:
                    conn.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
                    print(f"🗑️  删除表 {table_name}")
                except Exception as e:
                    print(f"⚠️  删除表 {table_name} 时出错: {e}")
        
        conn.commit()
        print("✅ 聊天相关数据表删除完成")
        return True
        
    except Exception as e:
        print(f"❌ 删除数据表失败: {e}")
        return False

def show_table_info():
    """显示表结构信息"""
    try:
        database_url = get_database_url()
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            # 获取所有表名
            result = conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name IN ('chat_sessions', 'chat_messages', 'work_flow_states')
                ORDER BY table_name
            """))
            
            tables = [row[0] for row in result]
            
            if not tables:
                print("❌ 未找到聊天相关的数据表")
                return
            
            print("📋 聊天相关数据表结构:")
            print("=" * 50)
            
            for table_name in tables:
                print(f"\n🔍 表: {table_name}")
                print("-" * 30)
                
                # 获取表结构
                result = conn.execute(text(f"""
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                    ORDER BY ordinal_position
                """))
                
                for row in result:
                    nullable = "NULL" if row[2] == "YES" else "NOT NULL"
                    default = f"DEFAULT {row[3]}" if row[3] else ""
                    print(f"  {row[0]}: {row[1]} {nullable} {default}")
        
    except Exception as e:
        print(f"❌ 获取表结构信息失败: {e}")

if __name__ == "__main__":
    print("=" * 60)
    print("聊天数据表管理工具")
    print("=" * 60)
    print("1. 创建聊天数据表")
    print("2. 删除聊天数据表")
    print("3. 显示表结构信息")
    print("4. 退出")
    print("-" * 60)
    
    while True:
        choice = input("请选择操作 (1-4): ").strip()
        
        if choice == "1":
            create_chat_tables()
            break
        elif choice == "2":
            drop_chat_tables()
            break
        elif choice == "3":
            show_table_info()
            break
        elif choice == "4":
            print("👋 退出程序")
            break
        else:
            print("❌ 无效选择，请重新输入")

