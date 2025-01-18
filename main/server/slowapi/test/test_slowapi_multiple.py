#! /usr/bin/python3


# temporary until SlowAPI becomes a package
import sys, os
sys.path.insert(1, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))


import slowapi


class Peach(slowapi.App):
    @slowapi.get('/hello')
    def hello(self):
        return ['I am a peach']

    
class Orange(slowapi.App):        
    @slowapi.get('/hello')
    def hello(self):
        return ['I am an orange']

    
class AbortHello(slowapi.App):        
    @slowapi.get('/hello')
    def hello(self, request:slowapi.Request):
        request.abort()

    
class MyApp(slowapi.App):
    def __init__(self):
        super().__init__()
        self.slowapi_append(Peach())
        #self.slowapi_append(AbortHello())
        self.slowapi_append(Orange())

    @slowapi.get('/hello')
    def hello(self):
        return ['Hello.']


    
app = MyApp()



if __name__ == '__main__':
    print(app.slowapi('/hello'))

    key = slowapi.BasicAuthentication.generate_key('slow', 'dash')
    print(key)
    app.slowapi_prepend(slowapi.BasicAuthentication(auth_list=[key]))
    
    app.run()
