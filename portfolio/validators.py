import re
from urllib.parse import urlsplit, parse_qs
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


def video_embed_url(value):
    if not value:
        return ''
    try:
        url = urlsplit(value)
        if url.scheme != 'https' or url.username or url.password or url.port:
            raise ValueError
        host = url.hostname
        video_id = ''
        if host in {'youtube.com', 'www.youtube.com', 'm.youtube.com'} and url.path == '/watch':
            video_id = parse_qs(url.query).get('v', [''])[0]
        elif host == 'youtu.be':
            video_id = url.path.strip('/')
        if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            return f'https://www.youtube-nocookie.com/embed/{video_id}'
        if host in {'vimeo.com', 'www.vimeo.com'} and re.fullmatch(r'/[0-9]{6,12}/?', url.path):
            return f'https://player.vimeo.com/video/{url.path.strip("/")}'
    except ValueError:
        pass
    raise ValidationError('Use an HTTPS YouTube watch/share link or a public Vimeo video link.')

def validate_video_url(value):
    video_embed_url(value)

def validate_image(value):
    opened_here = getattr(value, 'closed', False)
    if value.size > 5 * 1024 * 1024:
        raise ValidationError('Images must be 5 MB or smaller.')
    try:
        value.seek(0)
        with Image.open(value) as img:
            if img.format not in {'JPEG', 'PNG', 'WEBP'}:
                raise ValidationError('Upload a JPEG, PNG, or WebP image.')
            if img.width * img.height > 25_000_000:
                raise ValidationError('Image dimensions are too large (maximum 25 megapixels).')
            img.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError('Please upload a valid image.')
    finally:
        value.seek(0)
        if opened_here:
            value.close()
