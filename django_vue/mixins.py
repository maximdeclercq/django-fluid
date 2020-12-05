from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Dict, List

from bs4 import BeautifulSoup
from django.http import HttpRequest
from django.templatetags.static import static
from inflection import camelize


class DjangoVueComponentMixin:
    """A mixin that customizes rendering of a view to annotate children of blocks with
    it's name and to return a JSON with only the blocks if an AJAX request is made."""

    vue_components: Dict[str, any] = {}
    vue_data: Dict[str, any] = {}
    vue_emits: List[str] = []
    vue_props: List[str] = []
    vue_routes: OrderedDict[str, any] = OrderedDict()

    def get_vue_name(self):
        # TODO: Add package to name?
        return camelize(
            re.sub(r"(component|view)$", "", self.__class__.__name__, re.IGNORECASE)
        )

    def get_vue_definition(self, request, template=None, *args, **kwargs) -> str:
        return f"""
            const {self.get_vue_name()} = {{
              data() {{
                return {json.dumps(self.get_vue_data())}
              }},
              emits: {json.dumps(self.get_vue_emits())},
              props: {json.dumps(self.get_vue_props())},
              template: `{template or self.get_vue_template(request)}`,
            }}
        """

    def get_vue_components(self):
        return self.vue_components

    def get_vue_data(self):
        return self.vue_data

    def get_vue_emits(self):
        return self.vue_emits

    def get_vue_props(self):
        return self.vue_props

    def get_vue_routes(self):
        return self.vue_routes

    def get_vue_template(self, request, **kwargs):
        self.request = request
        context = self.get_context_data(**kwargs)
        response = self.render_to_response(context)
        response.render()
        soup = BeautifulSoup(response.content, "html5lib")

        body = soup.find("body")

        # TODO: What to do with styles and scripts from other views?
        _styles = [e.extract() for e in body.find_all("style")]
        _scripts = [e.extract() for e in body.find_all("script")]

        template = body.renderContents().decode("utf-8")

        # Replace brackets with curly braces so we don't have to override this in Vue
        return template.replace("[[", "{{").replace("]]", "}}")

    def get(self, request: HttpRequest, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        response.render()
        soup = BeautifulSoup(response.content, "html5lib")

        head = soup.find("head")

        def add_script_if_not_present(identifier: str, src: str) -> bool:
            if any(soup.find_all("script", src=lambda x: x and name in x)):
                head.append(soup.new_tag("script", attrs={"src": src}))

        # Add the required libraries to the head if they are not present
        add_script_if_not_present("axios", "https://unpkg.com/axios")
        add_script_if_not_present("vue", "https://unpkg.com/vue@next")
        add_script_if_not_present("vue-router", "https://unpkg.com/vue-router@next")
        add_script_if_not_present(
            "vue3-sfc-loader", "https://cdn.jsdelivr.net/npm/vue3-sfc-loader"
        )

        # Extract styles and scripts from body
        body = soup.find("body")
        styles = [e.extract() for e in body.find_all("style")]
        scripts = [e.extract() for e in body.find_all("script")]
        body.clear()

        # Construct Vue app
        vue = soup.new_tag("script")
        # Get unique component instances by their name
        instances = {
            c.get_vue_name(): c
            for c in list(self.get_vue_components().values())
            + list(self.get_vue_routes().values())
            + [self]
        }.values()
        definitions = "\n".join([c.get_vue_definition(request) for c in instances])
        # TODO: Do not globally register components but allow subcomponents
        registrations = "\n".join(
            f'app.component("{k}", {v.get_vue_name()})'
            for k, v in self.get_vue_components().items()
        )
        routes = ",".join(
            f'{{ path: "{k}", component: {v.get_vue_name()} }}'
            for k, v in self.get_vue_routes().items()
        )
        vue.string = f"""
            const {{ loadModule }} = window["vue3-sfc-loader"];
            {definitions}
            const app = Vue.createApp({self.get_vue_name()})
            {registrations}
            const router = VueRouter.createRouter({{
              history: VueRouter.createWebHashHistory(),
              routes: [{routes}]
            }})
            app.use(router)
            app.mount("#app")
        """

        # Construct new body
        body.extend(styles)
        body.append(soup.new_tag("div", id="app"))
        body.append(vue)
        body.extend(scripts)

        response.content = soup.renderContents().decode("utf-8")
        return response
