import { apiClient } from '@/utils/apiClient';

export interface WorkCreate {
  title: string;
  description?: string;
  tags?: string;
}

export interface WorkUpdate {
  title?: string;
  description?: string;
  status?: string;
  progress?: number;
  tags?: string;
}

export interface Work {
  id: number;
  work_id: string;
  title: string;
  description?: string;
  status: string;
  progress: number;
  tags?: string;
  created_at: string;
  updated_at: string;
  created_by: number;
}

export interface WorkListResponse {
  works: Work[];
  total: number;
  page: number;
  size: number;
}

export interface FileInfo {
  name: string;
  type: 'directory' | 'file';
  size?: number;
  modified: number;
  path: string;
}

export interface FileUploadResponse {
  message: string;
  path: string;
  size: number;
  filename: string;
}

export interface FileWriteResponse {
  message: string;
  path: string;
  size: number;
}

export interface DirectoryCreateResponse {
  message: string;
}

export interface FileDeleteResponse {
  message: string;
}

export interface WorkMetadata {
  work_id: string;
  created_at: string;
  status: string;
  progress: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
}

// 工作管理API
export const workspaceAPI = {
  // 创建工作
  async createWork(token: string, workData: WorkCreate): Promise<Work> {
    const response = await apiClient.request<Work>('/api/works', {
      method: 'POST',
      body: JSON.stringify(workData),
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  },

  // 获取工作列表
  async getWorks(
    token: string, 
    skip: number = 0, 
    limit: number = 100,
    status?: string,
    search?: string
  ): Promise<WorkListResponse> {
    const params = new URLSearchParams();
    params.append('skip', skip.toString());
    params.append('limit', limit.toString());
    if (status) params.append('status', status);
    if (search) params.append('search', search);

    const response = await apiClient.request<WorkListResponse>(`/api/works?${params.toString()}`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  },

  // 获取工作详情
  async getWork(token: string, workId: string): Promise<Work> {
    const response = await apiClient.request<Work>(`/api/works/${workId}`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  },

  // 更新工作信息
  async updateWork(token: string, workId: string, workData: WorkUpdate): Promise<Work> {
    const response = await apiClient.request<Work>(`/api/works/${workId}`, {
      method: 'PUT',
      body: JSON.stringify(workData),
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  },

  // 更新工作状态
  async updateWorkStatus(
    token: string, 
    workId: string, 
    status: string, 
    progress?: number
  ): Promise<Work> {
    const params = new URLSearchParams();
    params.append('status', status);
    if (progress !== undefined) params.append('progress', progress.toString());

    const response = await apiClient.request<Work>(`/api/works/${workId}/status?${params.toString()}`, {
      method: 'PATCH',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  },

  // 删除工作
  async deleteWork(token: string, workId: string): Promise<{ message: string }> {
    const response = await apiClient.request<{ message: string }>(`/api/works/${workId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  },

  // 获取工作元数据
  async getWorkMetadata(token: string, workId: string): Promise<WorkMetadata> {
    const response = await apiClient.request<WorkMetadata>(`/api/works/${workId}/metadata`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  },

  // 获取对话历史
  async getChatHistory(token: string, workId: string): Promise<ChatMessage[]> {
    const response = await apiClient.request<ChatMessage[]>(`/api/works/${workId}/chat-history`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  }
};

// 工作空间文件管理API
export const workspaceFileAPI = {
  // 列出文件
  async listFiles(
    token: string, 
    workId: string, 
    path: string = ''
  ): Promise<FileInfo[]> {
    const params = path ? `?path=${encodeURIComponent(path)}` : '';
    const response = await apiClient.request<FileInfo[]>(`/api/workspace/${workId}/files${params}`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  },

  // 读取文件
  async readFile(token: string, workId: string, filePath: string): Promise<string> {
    const response = await apiClient.request<{ content: string }>(`/api/workspace/${workId}/files/${encodeURIComponent(filePath)}`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response.content;
  },

  // 写入文件
  async writeFile(
    token: string, 
    workId: string, 
    filePath: string, 
    content: string
  ): Promise<FileWriteResponse> {
    const formData = new FormData();
    formData.append('content', content);

    const response = await apiClient.request<FileWriteResponse>(`/api/workspace/${workId}/files/${encodeURIComponent(filePath)}`, {
      method: 'POST',
      body: formData,
      headers: { 
        Authorization: `Bearer ${token}`
      }
    });
    return response;
  },

  // 上传文件
  async uploadFile(
    token: string, 
    workId: string, 
    filePath: string, 
    file: File
  ): Promise<FileUploadResponse> {
    const formData = new FormData();
    formData.append('file_path', filePath);
    formData.append('file', file);

    const response = await apiClient.request<FileUploadResponse>(`/api/workspace/${workId}/upload`, {
      method: 'POST',
      body: formData,
      headers: { 
        Authorization: `Bearer ${token}`
      }
    });
    return response;
  },

  // 删除文件
  async deleteFile(
    token: string, 
    workId: string, 
    filePath: string
  ): Promise<FileDeleteResponse> {
    const response = await apiClient.request<FileDeleteResponse>(`/api/workspace/${workId}/files/${encodeURIComponent(filePath)}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  },

  // 创建目录
  async createDirectory(
    token: string, 
    workId: string, 
    dirPath: string
  ): Promise<DirectoryCreateResponse> {
    const formData = new FormData();
    formData.append('dir_path', dirPath);

    const response = await apiClient.request<DirectoryCreateResponse>(`/api/workspace/${workId}/mkdir`, {
      method: 'POST',
      body: formData,
      headers: { 
        Authorization: `Bearer ${token}`
      }
    });
    return response;
  },

  // 获取文件信息
  async getFileInfo(
    token: string, 
    workId: string, 
    filePath: string
  ): Promise<FileInfo> {
    const response = await apiClient.request<FileInfo>(`/api/workspace/${workId}/files/${encodeURIComponent(filePath)}/info`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` }
    });
    return response;
  }
};

