# Created by Sanshiro Enomoto on 10 January 2025 #


import typing, inspect, copy, logging
from urllib.parse import urlparse, parse_qsl, unquote
import wsgiref

from .model import JSON, DictJSON
from .request import Request
from .response import Response
from .server import run as run_server



class PathRule:
    def __init__(self, rule:str, method:str, func_signature:inspect.Signature, *, status_code:int=200):
        self.rule_str = rule
        self.method = method.upper()
        self.status_code = status_code
        
        self.path = []
        self.path_params = {}  # {pos:int, name:str}
        self.param_attributes = {}  # {name: str, param: inspect.Parameter }
        self.take_extra_path = False
        
        self.bytes_body_param = None
        self.json_body_param = None
        self.json_dict_body_param = None
        self.request_param = None
        self.path_param = None
        self.query_param = None

        for elem in rule.split('/'):
            if elem is not None and len(elem) > 0:
                self.path.append(elem)

        if len(self.path) > 0 and self.path[-1] == '{*}':
            self.take_extra_path = True
            del self.path[-1]
                
        for pos, elem in enumerate(self.path):
            if len(elem) > 2 and elem[0] == '{' and elem[-1] == '}':
                self.path_params[pos] = elem[1:-1]
                self.path[pos] = None

        for index, pname in enumerate(func_signature.parameters):
            if index == 0:  # self
                continue
            param = func_signature.parameters[pname]
            if param.annotation is Request:   # to store request itself
                self.request_param = pname
            elif param.annotation is bytes:   # to store request body
                self.bytes_body_param = pname
            elif param.annotation in [JSON, DictJSON]:   # to decode request body as JSON
                self.json_body_param = pname
            elif param.annotation is list:  # to store URL path
                self.path_param = pname
            elif param.annotation is dict:  # to store URL query
                self.query_param = pname
            else:
                self.param_attributes[pname] = param

        logging.debug(f'PathRule {rule} --> {self.path}, {self.path_params}, {self.param_attributes}')
            

    def __str__(self):
        return self.rule_str
    

    def match(self, request:Request):
        # do not process aborted requests
        if request.aborted:
            return None
        
        # method match
        if request.method != self.method:
            return None
        
        # length match
        if len(request.path) != len(self.path):
            if (len(request.path) > len(self.path)) and not self.take_extra_path:
                return None
            # shorter path: maybe a parameter with a default value; check it
            for k in range(len(request.path), len(self.path)):
                if self.path[k] is not None:
                    return None
        
        # path name/param match
        params = {}
        for pos, value in enumerate(request.path):
            if pos >= len(self.path):
                pass
            elif self.path[pos] is None:
                params[self.path_params[pos]] = value
            elif self.path[pos] == value:
                pass
            else:
                return None

        params.update(request.query)

        # argument match / import
        kwargs = {}
        for pname, attr in self.param_attributes.items():
            value = params.get(pname, None)
            if value is None and attr.default is not inspect._empty:
                value = attr.default
            if value is not None and attr.annotation is not inspect._empty:
                try:
                    # BUG: this does not work if the type is "Optional[xxx]"
                    value = attr.annotation(value)
                except Exception as e:
                    logging.warning(f'App: incompatible parameter type: {pname}: {e}')
                    return None
            kwargs[pname] = value
            
        # special arguments
        if self.request_param is not None:
            kwargs[self.request_param] = request
        if self.bytes_body_param is not None:
            kwargs[self.bytes_body_param] = request.body
        if self.json_body_param is not None:
            Type = self.param_attributes[self.json_body_param].annotation
            doc = Type(request.body)
            if doc.value() is None:
                return None
            else:
                kwargs[self.json_body_param] = doc
        if self.path_param is not None:
            kwargs[self.path_param] = copy.deepcopy(request.path)
        if self.query_param is not None:
            kwargs[self.query_param] = copy.deepcopy(request.query)

        return kwargs
    


def get(path_rule:str, status_code:int=200):
    """decorator to make a GET-request handler (method of a subclass of App)
    Args:
      - path_rule: path pattern to match
      - status_code: default status code for success
    """
    def wrapper(func):
        if not hasattr(func, 'slowapi_path_rule'):
            func.slowapi_path_rule = PathRule(path_rule, 'GET', inspect.signature(func), status_code=status_code)
        return func
    return wrapper


def post(path_rule:str, status_code:int=201):
    """decorator to make a POST-request handler (method of a subclass of App)
    Args:
      - path_rule: path pattern to match
      - status_code: default status code for success
    """
    def wrapper(func):
        if not hasattr(func, 'slowapi_path_rule'):
            func.slowapi_path_rule = PathRule(path_rule, 'POST', inspect.signature(func), status_code=status_code)
        return func
    return wrapper


def delete(path_rule:str, status_code:int=200):
    """decorator to make a DELETE-request handler (method of a subclass of App)
    Args:
      - path_rule: path pattern to match
      - status_code: default status code for success
    """
    def wrapper(func):
        if not hasattr(func, 'slowapi_path_rule'):
            func.slowapi_path_rule = PathRule(path_rule, 'DELETE', inspect.signature(func), status_code=status_code)
        return func
    return wrapper

    

class App:
    def __init__(self):
        self.slowapi_handlers = []
        self.slowapi_prepended_apps = []
        self.slowapi_appended_apps = []

        # Binding URL handlers to the PathRule attached by decorators (@get(PATH) etc).
        # Note that __init__() is called after all the decorators.
        for name, method in inspect.getmembers(type(self), predicate=inspect.isfunction):
            if hasattr(method, 'slowapi_path_rule'):
                self.slowapi_handlers.append(method)

        
    def slowapi(self, request:Request, body:bytes=None) -> Response:
        if not hasattr(self, 'slowapi_handlers'):
            # in case the __init__() method is not called by user subclass
            self.__init__()
        if type(request) is str:
            if body is None:
                request = Request(request, method='GET')
            else:
                request = Request(request, method='POST', body=body)

        response = Response()
        for app in self.slowapi_prepended_apps:
            response.append(app.slowapi(request))
            
        for handler in self.slowapi_handlers:
            args = handler.slowapi_path_rule.match(request)
            if args is not None:
                this_response = Response(status_code=handler.slowapi_path_rule.status_code)
                this_response.append(handler(self, **args))
                response.append(this_response)
            
        for app in self.slowapi_appended_apps:
            response.append(app.slowapi(request))
            
        return response


    def slowapi_prepend(self, app):
        if not hasattr(self, 'slowapi_handlers'):
            # in case the __init__() method is not called by user subclass
            self.__init__()
        self.slowapi_prepended_apps.insert(0, app)


    def slowapi_append(self, app):
        if not hasattr(self, 'slowapi_handlers'):
            # in case the __init__() method is not called by user subclass
            self.__init__()
        self.slowapi_appended_apps.append((app))


    def slowapi_apps(self):
        for app in self.slowapi_prepended_apps:
            yield app
        for app in self.slowapi_appended_apps:
            yield app


    def __call__(self, environ, start_response):
        """WSGI interface
        Args: see the WSGI specification
        """
        url = wsgiref.util.request_uri(environ)
        method = environ.get('REQUEST_METHOD', 'GET')

        body = None
        if method == 'POST':
            try:
                content_length = int(environ.get('CONTENT_LENGTH', ''))
            except:
                logging.error(f'WSGI_POST: bad content length: {content_length}')
                start_response('400 Bad Request', [])
                return [ b'' ]
            if content_length > 1024*1024*1024:
                logging.error(f'WSGI_POST: content length too large: {content_length}')
                start_response('507 Insufficient Storage', [])
                return [ b'' ]
            if content_length > 0:
                body = environ['wsgi.input'].read(content_length)
            
        response = self.slowapi(Request(url, method=method, body=body))
    
        start_response(response.get_status(), response.get_headers())
        return [ response.get_content() ]
    

    def run(self, **kwargs):
        kwargs['port'] = kwargs.get('port', 8000)
        kwargs['api_path'] = kwargs.get('api_path', None)
        kwargs['webfile_dir'] = kwargs.get('webfile_dir', None)
        run_server(self, **kwargs)
