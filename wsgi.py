def app(environ, start_response):
    status = '200 OK'
    output = b'WSGI works'

    headers = [
        ('Content-type', 'text/plain; charset=utf-8'),
        ('Content-Length', str(len(output)))
    ]
    start_response(status, headers)
    return [output]