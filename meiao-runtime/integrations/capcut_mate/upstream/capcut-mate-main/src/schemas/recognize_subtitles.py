from pydantic import BaseModel, Field


class RecognizeSubtitlesRequest(BaseModel):
    """触发剪映智能字幕识别"""
    draft_url: str = Field(default="", description="草稿URL")
    draft_name: str = Field(default="", description="剪映草稿名称；为空时从 draft_url 提取 draft_id")
    timeout: float = Field(default=180, description="等待识别完成的超时时间，单位秒")


class RecognizeSubtitlesResponse(BaseModel):
    """剪映智能字幕识别响应"""
    draft_url: str = Field(default="", description="草稿URL")
    draft_name: str = Field(default="", description="剪映草稿名称")
    recognized: bool = Field(default=False, description="是否已触发并完成识别")
