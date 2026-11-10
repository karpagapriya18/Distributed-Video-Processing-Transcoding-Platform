from pydantic import BaseModel, Field, field_validator


class ProcessRequest(BaseModel):
    resolutions: list[int] = Field(default_factory=lambda: [360, 720, 1080], min_length=1, max_length=3)

    @field_validator('resolutions')
    @classmethod
    def allowed(cls, value):
        if len(set(value)) != len(value) or any(x not in (360, 720, 1080) for x in value):
            raise ValueError('Choose unique resolutions from 360, 720, 1080')
        return sorted(value)
