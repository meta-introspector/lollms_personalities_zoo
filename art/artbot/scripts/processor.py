import subprocess
from pathlib import Path
from lollms.helpers import ASCIIColors, trace_exception
from lollms.config import TypedConfig, BaseConfig, ConfigTemplate, InstallOption
from lollms.types import MSG_TYPE
from lollms.personality import APScript, AIPersonality
from lollms.utilities import PromptReshaper, git_pull
import re
import importlib
import requests
from tqdm import tqdm
import webbrowser

class Processor(APScript):
    """
    A class that processes model inputs and outputs.

    Inherits from APScript.
    """
    def __init__(
                 self, 
                 personality: AIPersonality,
                 callback = None,
                ) -> None:
        # Get the current directory
        root_dir = personality.lollms_paths.personal_path
        # We put this in the shared folder in order as this can be used by other personalities.
        shared_folder = root_dir/"shared"
        self.sd_folder = shared_folder / "auto_sd"
        
        self.callback = None
        self.sd = None
        self.previous_sd_positive_prompt = None
        self.sd_negative_prompt = None

        personality_config_template = ConfigTemplate(
            [
                {"name":"imagine","type":"bool","value":True,"help":"Imagine the images"},
                {"name":"paint","type":"bool","value":True,"help":"Paint the images"},
                {"name":"use_fixed_negative_prompts","type":"bool","value":True,"help":"Uses parisNeo's preferred negative prompts"},
                {"name":"show_infos","type":"bool","value":True,"help":"Shows generation informations"},
                {"name":"continuous_discussion","type":"bool","value":True,"help":"If true then previous prompts and infos are taken into acount to generate the next image"},
                {"name":"automatic_resolution_selection","type":"bool","value":True,"help":"If true then artbot chooses the resolution of the image to generate"},
                {"name":"add_style","type":"bool","value":True,"help":"If true then artbot will choose and add a specific style to the prompt"},
                
                {"name":"activate_discussion_mode","type":"bool","value":True,"help":"If active, the AI will not generate an image until you ask it to, it will just talk to you until you ask it to make an artwork"},
                
                {"name":"continue_from_last_image","type":"bool","value":False,"help":"Uses last image as input for next generation"},
                {"name":"img2img_denoising_strength","type":"float","value":7.5, "min":0.01, "max":1.0, "help":"The image to image denoising strength"},
                {"name":"restore_faces","type":"bool","value":True,"help":"Restore faces"},
                {"name":"caption_received_files","type":"bool","value":False,"help":"If active, the received file will be captioned"},
                {"name":"sampler_name","type":"str","value":"Euler a", "options":["Euler a","Euler","LMS","Heun","DPM2","DPM2 a","DPM++ 2S a","DPM++ 2M","DPM++ SDE","DPM++ 2M SDE", "DPM fast", "DPM adaptive", "DPM Karras", "DPM2 Karras", "DPM2 a Karras","DPM++ 2S a Karras","DPM++ 2M Karras","DPM++ SDE Karras","DPM++ 2M SDE Karras" ,"DDIM", "PLMS","UniPC"], "help":"Select the sampler to be used for the diffusion operation. Supported samplers ddim, dpms, plms"},                
                {"name":"steps","type":"int","value":50, "min":10, "max":1024},
                {"name":"scale","type":"float","value":7.5, "min":0.1, "max":100.0},

                {"name":"width","type":"int","value":512, "min":10, "max":2048},
                {"name":"height","type":"int","value":512, "min":10, "max":2048},

                {"name":"thumbneil_width","type":"int","value":256, "min":10, "max":2048},
                {"name":"thumbneil_height","type":"int","value":256, "min":10, "max":2048},

                {"name":"automatic_image_size","type":"bool","value":False,"help":"If true, artbot will select the image resolution"},
                {"name":"skip_grid","type":"bool","value":True,"help":"Skip building a grid of generated images"},
                {"name":"batch_size","type":"int","value":1, "min":1, "max":100,"help":"Number of images per batch (requires more memory)"},
                {"name":"num_images","type":"int","value":1, "min":1, "max":100,"help":"Number of batch of images to generate (to speed up put a batch of n and a single num images, to save vram, put a batch of 1 and num_img of n)"},
                {"name":"seed","type":"int","value":-1},
                {"name":"max_generation_prompt_size","type":"int","value":512, "min":10, "max":personality.config["ctx_size"]},
                
            ]
            )
        personality_config_vals = BaseConfig.from_template(personality_config_template)

        personality_config = TypedConfig(
            personality_config_template,
            personality_config_vals
        )
        super().__init__(
                            personality,
                            personality_config,
                            [
                                {
                                    "name": "idle",
                                    "commands": { # list of commands
                                        "help":self.help,
                                        "new_image":self.new_image,
                                        "show_sd":self.show_sd,
                                        "regenerate":self.regenerate,
                                        "show_settings":self.show_settings,
                                    },
                                    "default": self.main_process
                                },                           
                            ],
                            callback=callback
                        )
        self.width=int(self.personality_config.width)
        self.height=int(self.personality_config.height)


    def install(self):
        super().install()
        
        requirements_file = self.personality.personality_package_path / "requirements.txt"
        # Install dependencies using pip from requirements.txt
        subprocess.run(["pip", "install", "--upgrade", "-r", str(requirements_file)])      

        # Clone repository
        if not self.sd_folder.exists():
            subprocess.run(["git", "clone", "https://github.com/ParisNeo/stable-diffusion-webui.git", str(self.sd_folder)])
        self.prepare()
        ASCIIColors.success("Installed successfully")


    def prepare(self):
        if self.sd is None:
            self.step_start("Loading ParisNeo's fork of AUTOMATIC1111's stable diffusion service")
            self.sd = self.get_sd().LollmsSD(self.personality.lollms_paths, "Artbot", max_retries=-1)
            self.step_end("Loading ParisNeo's fork of AUTOMATIC1111's stable diffusion service")
        
        
    def get_sd(self):
        
        sd_script_path = self.sd_folder / "lollms_sd.py"
        git_pull(self.sd_folder)
        
        if sd_script_path.exists():
            module_name = sd_script_path.stem  # Remove the ".py" extension
            # use importlib to load the module from the file path
            loader = importlib.machinery.SourceFileLoader(module_name, str(sd_script_path))
            sd_module = loader.load_module()
            return sd_module

    def remove_image_links(self, markdown_text):
        # Regular expression pattern to match image links in Markdown
        image_link_pattern = r"!\[.*?\]\((.*?)\)"

        # Remove image links from the Markdown text
        text_without_image_links = re.sub(image_link_pattern, "", markdown_text)

        return text_without_image_links


    def help(self, prompt, full_context):
        self.full(self.personality.help)
    
    def new_image(self, prompt, full_context):
        self.files=[]
        self.full("Starting fresh :)")
        
        
    def show_sd(self, prompt, full_context):
        self.prepare()
        webbrowser.open("http://127.0.0.1:7860/?__theme=dark")        
        self.full("Showing Stable diffusion UI")
        
        
    def show_settings(self, prompt, full_context):
        self.prepare()
        webbrowser.open("http://127.0.0.1:7860/?__theme=dark")        
        self.full("Showing Stable diffusion settings UI")
        
    def show_last_image(self, prompt, full_context):
        self.prepare()
        if len(self.files)>0:
            self.full(f"![]({self.files})")        
        else:
            self.full("Showing Stable diffusion settings UI")        
        
    def add_file(self, path, callback=None):
        if callback is None and self.callback is not None:
            callback = self.callback

        self.prepare()
        super().add_file(path)
        if self.personality_config.caption_received_files:
            self.new_message("", MSG_TYPE.MSG_TYPE_CHUNK, callback=callback)
            self.step_start("Understanding the image", callback=callback)
            description = self.sd.interrogate(str(path)).info
            ASCIIColors.yellow(description)
            self.step_end("Understanding the image", callback=callback)
            pth = str(path).replace("\\","/").split('/')
            idx = pth.index("uploads")
            pth = "/".join(pth[idx:])


            
            file_path = f"""<div class="flex justify-center items-center cursor-pointer">
    <img id="Artbot_912" src="/{pth}" alt="Artbot generated image" class="object-cover" style="width:{self.personality_config.thumbneil_width}px;height:{self.personality_config.thumbneil_height}px">
</div>\n"""

            self.full(f"File added successfully\nImage description :\n{description}\nImage:\n!{file_path}", callback=callback)
            self.finished_message()
        else:    
            self.full(f"File added successfully\n", callback=callback)
        
    def regenerate(self, prompt, full_context):
        if self.previous_sd_positive_prompt:
            files, out, infos = self.sd.paint(
                            self.previous_sd_positive_prompt, 
                            self.previous_sd_negative_prompt,
                            self.files,
                            "",
                            sampler_name = self.personality_config.sampler_name,
                            num_images = self.personality_config.num_images,
                            seed = self.personality_config.seed,
                            scale = self.personality_config.scale,
                            steps = self.personality_config.steps,
                            img2img_denoising_strength = self.personality_config.img2img_denoising_strength,
                            width = self.personality_config.width,
                            height = self.personality_config.height,
                            thumbneil_width = self.personality_config.thumbneil_width,
                            thumbneil_height = self.personality_config.thumbneil_height,
                            restore_faces = self.personality_config.restore_faces,
                            step_start_callback = self.step_start,
                            step_end_callback = self.step_end,
                            file_ready_callback = self.full,
                        )

            self.full(out)
            if self.personality_config.show_infos:
                self.new_message("infos", MSG_TYPE.MSG_TYPE_JSON_INFOS,infos)
        else:
            self.full("Please generate an image first then retry")

    

    def get_styles(self, prompt, full_context):
        self.step_start("Selecting style")
        styles=[
            "Oil painting",
            "Octane rendering",
            "Cinematic",
            "Art deco",
            "Enameled",
            "Etching",
            "Arabesque",
            "Cross Hatching",
            "Callegraphy",
            "Vector art",
            "Vexel art",
            "Cartoonish",
            "Cubism",
            "Surrealism",
            "Pop art",
            "Pop surrealism",
            "Roschach Inkblot",
            "Flat icon",
            "Material Design Icon",
            "Skeuomorphic Icon",
            "Glyph Icon",
            "Outline Icon",
            "Gradient Icon",
            "Neumorphic Icon",
            "Vintage Icon",
            "Abstract Icon"

        ]
        stl=", ".join(styles)
        prompt=f"{full_context}\n!@>user:{prompt}\nSelect what style(s) among those is more suitable for this artwork: {stl}\n!@>assistant:I select"
        stl = self.generate(prompt, self.personality_config.max_generation_prompt_size).strip().replace("</s>","").replace("<s>","")
        self.step_end("Selecting style")
        return stl

    def get_resolution(self, prompt, full_context, default_resolution=[512,512]):

        def extract_resolution(text, default_resolution=[512, 512]):
            # Define a regular expression pattern to match the (w, h) format
            pattern = r'\((\d+),\s*(\d+)\)'
            
            # Search for the pattern in the text
            match = re.search(pattern, text)
            
            if match:
                width = int(match.group(1))
                height = int(match.group(2))
                return width, height
            else:
                return default_resolution
                    
        self.step_start("Choosing resolution")
        prompt=f"{full_context}\n!@>user:{prompt}\nSelect a suitable image size (width, height).\nThe default resolution uis ({default_resolution[0]},{default_resolution[1]})\n!@>selected_image_size:"
        sz = self.generate(prompt, self.personality_config.max_generation_prompt_size).strip().replace("</s>","").replace("<s>","").split("\n")[0]

        self.step_end("Choosing resolution")

        return extract_resolution(sz, default_resolution)

    def main_process(self, initial_prompt, full_context):    
        self.prepare()
        full_context = full_context[:full_context.index(initial_prompt)]
        
        if self.personality_config.imagine:
            if self.personality_config.activate_discussion_mode:
                pr  = PromptReshaper("""!@>discussion:
{{previous_discussion}}{{initial_prompt}}
!@>question: Is the user's message asking to generate an image? 
!@>instruction>artbot should answer with Yes or No.
!@>artbot:The answer to the question is""")
                prompt = pr.build({
                        "previous_discussion":full_context,
                        "initial_prompt":initial_prompt
                        }, 
                        self.personality.model.tokenize, 
                        self.personality.model.detokenize, 
                        self.personality.model.config.ctx_size,
                        ["previous_discussion"]
                        )
                is_discussion = self.generate(prompt, self.personality_config.max_generation_prompt_size).strip().replace("</s>","").replace("<s>","")
                ASCIIColors.cyan(is_discussion)
                if "yes" not in is_discussion.lower():
                    pr  = PromptReshaper("""!@>instructions>Artbot est une IA conviviale de génération d'art qui discute des idées avec les humains sur l'art.
!@>discussion:
{{previous_discussion}}{{initial_prompt}}
!@>artbot:""")
                    prompt = pr.build({
                            "previous_discussion":full_context,
                            "initial_prompt":initial_prompt
                            }, 
                            self.personality.model.tokenize, 
                            self.personality.model.detokenize, 
                            self.personality.model.config.ctx_size,
                            ["previous_discussion"]
                            )
                    ASCIIColors.yellow(prompt)

                    response = self.generate(prompt, self.personality_config.max_generation_prompt_size).strip().replace("</s>","").replace("<s>","")
                    self.full(response)
                    return



            if self.personality_config.automatic_resolution_selection:
                res = self.get_resolution(initial_prompt, full_context, [self.personality_config.width,self.personality_config.height])
                self.width=res[0]
                self.height=res[1]
            else:
                self.width=self.personality_config.width
                self.height=self.personality_config.height
            self.full(f"### Chosen resolution:\n{self.width}x{self.height}")         
            # ====================================================================================
            if self.personality_config.add_style:
                styles = self.get_styles(initial_prompt,full_context)
            else:
                styles = "No specific style selected."
            self.full(f"### Chosen resolution:\n{self.width}x{self.height}\n### Chosen style:\n{styles}")         

            self.step_start("Imagining positive prompt")
            # 1 first ask the model to formulate a query
            past = "!@>".join(self.remove_image_links(full_context).split("!@>")[:-2])
            pr  = PromptReshaper("""!@>instructions:
Make a prompt based on the discussion with the user presented below.
Make sure you mention every thing asked by the user's idea.
Do not make a very long text.
Follow this format in a single paragraph:
First describe the image, then write a list of words to make a generic description of the style and vibe (example: cyberpunk, steampunk, water painting, pensil drawing etc).
Then add words that describe the quality of the image such as detailed, high resolution, 4k, 8k. this is mandatory.
Then mention the type of the image, such as artwork, photorealistic, water painting, oil painting, pensil drawing, octane rendering etc.
Optionally mention the tool used to make the image or rendering, like unreal engine, or a specific camera type etc.
Optionally, you can also mention an artist or an art style. Do not write artistname, explicitly write the artist name if you need to or just omit this one.
To give more importance to a term put it inside multiple brackets (). More brackets mean more importance, for example ((word)) is more important than (word).
Do not use bullet points.
The prompt should be in english.
{{previous_discussion}}{{initial_prompt}}
!@>style_choice: {{styles}}                                 
!@>art_generation_prompt: Create""")
            prompt = pr.build({
                    "previous_discussion":past if self.personality_config.continuous_discussion else '',
                    "initial_prompt":initial_prompt,
                    "styles":styles
                    }, 
                    self.personality.model.tokenize, 
                    self.personality.model.detokenize, 
                    self.personality.model.config.ctx_size,
                    ["previous_discussion"]
                    )
            
            ASCIIColors.yellow(prompt)
            sd_positive_prompt = self.generate(prompt, self.personality_config.max_generation_prompt_size).strip().replace("</s>","").replace("<s>","")
            self.step_end("Imagining positive prompt")
            self.full(f"### Chosen resolution:\n{self.width}x{self.height}\n### Chosen style:\n{styles}\n### Positive prompt:\n{sd_positive_prompt}")         
            # ====================================================================================
            # ====================================================================================
            if not self.personality_config.use_fixed_negative_prompts:
                self.step_start("Imagining negative prompt")
                # 1 first ask the model to formulate a query
                pr  = PromptReshaper("""!@>instructions:
    Generate negative prompt based on the discussion with the user.
    The negative prompt is a list of keywords that should not be present in our image.
    Try to force the generator not to generate text or extra fingers or deformed faces.
    Use as many words as you need depending on the context.
    To give more importance to a term put it ibti multiple brackets ().
    example: ((((ugly)))), (((duplicate))), ((morbid)), ((mutilated)), out of frame, extra fingers, mutated hands, ((poorly drawn hands)), ((poorly drawn face)), (((mutation))), (((deformed))), ((ugly)), blurry, ((bad anatomy)), (((bad proportions))), ((extra limbs)), cloned face, (((disfigured))), out of frame, ugly, extra limbs, (bad anatomy), gross proportions, (malformed limbs), ((missing arms)), ((missing legs)), (((extra arms))), (((extra legs))), mutated hands, (fused fingers), (too many fingers), (((long neck)))
    !@>discussion:
    {{previous_discussion}}{{initial_prompt}}
    !@>artbot:
    prompt:{{sd_positive_prompt}}{{styles}}
    negative_prompt: ((morbid)),""")
                prompt = pr.build({
                        "previous_discussion":self.remove_image_links(full_context),
                        "initial_prompt":initial_prompt,
                        "sd_positive_prompt":sd_positive_prompt,
                        "styles":','+styles if styles!='' else ''
                        }, 
                        self.personality.model.tokenize, 
                        self.personality.model.detokenize, 
                        self.personality.model.config.ctx_size,
                        ["previous_discussion"]
                        )
                ASCIIColors.yellow(prompt)
                sd_negative_prompt = "blurry,"+self.generate(prompt, self.personality_config.max_generation_prompt_size).strip().replace("</s>","").replace("<s>","")
                self.step_end("Imagining negative prompt")
            else:
                sd_negative_prompt = "((((ugly)))), (((duplicate))), ((morbid)), ((mutilated)), out of frame, extra fingers, mutated hands, ((poorly drawn hands)), ((poorly drawn face)), (((mutation))), (((deformed))), ((ugly)), blurry, ((bad anatomy)), (((bad proportions))), ((extra limbs)), cloned face, (((disfigured))), out of frame, ugly, extra limbs, (bad anatomy), gross proportions, (malformed limbs), ((missing arms)), ((missing legs)), (((extra arms))), (((extra legs))), mutated hands, (fused fingers), (too many fingers), (((long neck)))"
            self.full(f"### Chosen resolution:\n{self.width}x{self.height}\n### Chosen style:\n{styles}\n### Positive prompt:\n{sd_positive_prompt}\n### Negative prompt:\n{sd_negative_prompt}")         
            # ====================================================================================            
            
        else:
            self.width=self.personality_config.width
            self.height=self.personality_config.height
            prompt = initial_prompt.split("\n")
            if len(prompt)>1:
                sd_positive_prompt = prompt[0]
                sd_negative_prompt = prompt[1]
            else:
                sd_positive_prompt = prompt[0]
                sd_negative_prompt = ""
            
        self.previous_sd_positive_prompt = sd_positive_prompt
        self.previous_sd_negative_prompt = sd_negative_prompt

        output = f"### Positive prompt :\n{sd_positive_prompt}\n### Negative prompt :\n{sd_negative_prompt}\n"

        if self.personality_config.paint:
            files, output, infos = self.sd.paint(
                            sd_positive_prompt, 
                            sd_negative_prompt,
                            self.files,
                            output,
                            sampler_name = self.personality_config.sampler_name,
                            num_images = self.personality_config.num_images,
                            seed = self.personality_config.seed,
                            scale = self.personality_config.scale,
                            steps = self.personality_config.steps,
                            img2img_denoising_strength = self.personality_config.img2img_denoising_strength,
                            width = self.personality_config.width,
                            height = self.personality_config.height,
                            thumbneil_width = self.personality_config.thumbneil_width,
                            thumbneil_height = self.personality_config.thumbneil_height,
                            restore_faces = self.personality_config.restore_faces,
                            step_start_callback = self.step_start,
                            step_end_callback = self.step_end,
                            file_ready_callback = self.full,
                        )

            if self.personality_config.continue_from_last_image:
                self.files= [files[-1]]            
        else:
            infos = None
        self.full(output.strip())
        if self.personality_config.show_infos and infos:
            self.json("infos", infos)


    def run_workflow(self, prompt, previous_discussion_text="", callback=None):
        """
        Runs the workflow for processing the model input and output.

        This method should be called to execute the processing workflow.

        Args:
            prompt (str): The input prompt for the model.
            previous_discussion_text (str, optional): The text of the previous discussion. Default is an empty string.
            callback a callback function that gets called each time a new token is received
        Returns:
            None
        """
        self.callback = callback
        self.process_state(prompt, previous_discussion_text, callback)

        return ""

