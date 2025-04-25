"""
Schema fixing utilities for MCP tools.

This module contains functions to fix schema validation issues between Google ADK
and MCP tools, particularly handling cases where MCP tools return schema types
as lists (e.g., ['string', 'number']) which are not compatible with Google ADK.
"""

import logging
import os
import inspect
from typing import List, Dict, Any, Optional, Tuple, Union
from google.adk.tools.mcp_tool.mcp_tool import MCPTool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset

# Set up logging
logger = logging.getLogger(__name__)

class SchemaFixOptions:
    """Options for schema fixing."""
    
    def __init__(
        self,
        convert_type_arrays_to: str = 'string',
        validate_api_key: bool = True,
        verbose: bool = False,
        aggressive: bool = True
    ):
        """
        Initialize schema fix options.
        
        Args:
            convert_type_arrays_to: The type to convert arrays to (default: 'string')
            validate_api_key: Whether to validate the OpenAI API key
            verbose: Whether to log verbose information
            aggressive: Whether to apply more aggressive schema fixing
        """
        self.convert_type_arrays_to = convert_type_arrays_to
        self.validate_api_key = validate_api_key
        self.verbose = verbose
        self.aggressive = aggressive


def fix_openai_validation_errors(obj, options=None, path=''):
    """
    Fix specific validation errors that OpenAI commonly reports.
    
    Args:
        obj: The object to fix
        options: SchemaFixOptions
        path: Current path for debugging
    """
    if not isinstance(obj, dict):
        return
        
    # Fix for: "In context=('properties', 'args'), array schema missing items"
    if 'type' in obj and obj['type'] == 'array' and 'items' not in obj:
        obj['items'] = {'type': 'string'}
        logger.warning(f"Fixed array schema missing items at {path}")
        
    # Check properties dictionary
    if 'properties' in obj and isinstance(obj['properties'], dict):
        props = obj['properties']
        
        # Fix args property specifically (common in contractCall error)
        if 'args' in props and isinstance(props['args'], dict):
            args_prop = props['args']
            if args_prop.get('type') == 'array' and 'items' not in args_prop:
                args_prop['items'] = {'type': 'string'}
                logger.warning(f"Fixed args array missing items at {path}.properties.args")
        
        # Process each property
        for prop_name, prop_schema in props.items():
            if isinstance(prop_schema, dict):
                fix_openai_validation_errors(prop_schema, options, f"{path}.properties.{prop_name}")

    # Process nested objects
    for key, value in obj.items():
        if isinstance(value, dict):
            fix_openai_validation_errors(value, options, f"{path}.{key}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    fix_openai_validation_errors(item, options, f"{path}.{key}[{i}]")


def fix_schema_types_recursive(obj: Any, options: SchemaFixOptions = None, path: str = '') -> None:
    """
    Recursively fix type arrays in schema dictionaries.
    
    Args:
        obj: The object to fix (dict or list)
        options: Schema fix options
        path: Current path in the schema (for debugging)
    """
    if options is None:
        options = SchemaFixOptions()
        
    if isinstance(obj, dict):
        # First fix OpenAI validation errors
        fix_openai_validation_errors(obj, options, path)
        
        # If the 'type' field is a list, convert it to the specified type
        if 'type' in obj and isinstance(obj['type'], list):
            if options.verbose and logger.level <= logging.DEBUG:
                logger.debug(f"Converting type array at {path}: {obj['type']} to '{options.convert_type_arrays_to}'")
            obj['type'] = options.convert_type_arrays_to
            
        # Special handling for enums that should be strings
        if options.aggressive and 'enum' in obj and isinstance(obj.get('enum'), list):
            # Make sure all enum values are strings
            obj['enum'] = [str(val) if not isinstance(val, str) else val for val in obj['enum']]
            
        # Fix for array type schemas that are missing 'items'
        if options.aggressive and obj.get('type') == 'array' and 'items' not in obj:
            # Add a default items schema for arrays
            obj['items'] = {'type': 'string'}
            if logger.level <= logging.INFO:
                logger.info(f"Fixed array schema missing items at {path}")
            
        # Process all nested dictionaries and lists
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                new_path = f"{path}.{k}" if path else k
                fix_schema_types_recursive(v, options, new_path)
    
    elif isinstance(obj, list):
        # Process all items in the list
        for i, item in enumerate(obj):
            if isinstance(item, (dict, list)):
                new_path = f"{path}[{i}]"
                fix_schema_types_recursive(item, options, new_path)


def patch_object_attributes(obj, name, options, visited=None):
    """
    Recursively patch attributes of an object to fix schema types.
    
    Args:
        obj: The object to patch
        name: The name of the object (for debugging)
        options: Schema fix options
        visited: Set of visited objects to avoid circular references
    """
    if visited is None:
        visited = set()
        
    # Check if we've already visited this object
    obj_id = id(obj)
    if obj_id in visited:
        return
    
    # Add to visited set
    visited.add(obj_id)
    
    # Look through attributes
    for attr_name in dir(obj):
        # Skip private attributes and methods
        if attr_name.startswith('_') or callable(getattr(obj, attr_name, None)):
            continue
            
        try:
            attr = getattr(obj, attr_name)
            
            # Fix any dictionary schemas
            if isinstance(attr, dict):
                if options.verbose:
                    logger.debug(f"Checking dictionary attribute: {name}.{attr_name}")
                fix_schema_types_recursive(attr, options, f"{name}.{attr_name}")
                
            # Fix any list schemas
            elif isinstance(attr, list):
                if options.verbose:
                    logger.debug(f"Checking list attribute: {name}.{attr_name}")
                fix_schema_types_recursive(attr, options, f"{name}.{attr_name}")
                
            # Recursively patch nested objects
            elif attr is not None and not isinstance(attr, (str, int, float, bool)):
                patch_object_attributes(attr, f"{name}.{attr_name}", options, visited)
                
        except Exception as e:
            if options.verbose:
                logger.debug(f"Error accessing/fixing attribute {name}.{attr_name}: {e}")


def patch_mcp_tool(tool: MCPTool, options: SchemaFixOptions = None) -> MCPTool:
    """
    Patch a single MCP tool to fix schema validation issues.
    
    Args:
        tool: The MCP tool to patch
        options: Schema fix options
    
    Returns:
        The patched MCP tool
    """
    if options is None:
        options = SchemaFixOptions()
    
    # Special handling for contractCall function which has a known issue
    if tool.name == "contractCall":
        if hasattr(tool, "_schema") and isinstance(tool._schema, dict):
            # Fix the schema directly
            schema = tool._schema
            
            # Navigate to args property if it exists
            if "properties" in schema and "args" in schema["properties"]:
                args_prop = schema["properties"]["args"]
                
                # Check if it's an array without items
                if args_prop.get("type") == "array" and "items" not in args_prop:
                    args_prop["items"] = {"type": "string"}
                    logger.info("Fixed contractCall args array schema")
    
    # Save the original method
    original_get_declaration = tool._get_declaration
    
    # Define a patched method
    def patched_get_declaration(self=tool):
        # Call the original method
        declaration = original_get_declaration()
        
        # Special handling for contractCall function
        if tool.name == "contractCall":
            # Try to find and fix the args property
            if hasattr(declaration, 'parameters') and declaration.parameters:
                if hasattr(declaration.parameters, 'schema_'):
                    schema_dict = declaration.parameters.schema_
                    if isinstance(schema_dict, dict) and "properties" in schema_dict:
                        if "args" in schema_dict["properties"]:
                            args_prop = schema_dict["properties"]["args"]
                            if args_prop.get("type") == "array" and "items" not in args_prop:
                                args_prop["items"] = {"type": "string"}
                                logger.info("Fixed contractCall args array schema in declaration")
        
        # Access the schema_dict directly if it exists
        if hasattr(declaration, 'parameters') and declaration.parameters:
            if hasattr(declaration.parameters, 'schema_'):
                schema_dict = declaration.parameters.schema_
                if options.verbose and logger.level <= logging.DEBUG:
                    logger.debug(f"Fixing schema for tool: {tool.name}")
                
                # Apply the schema fix
                fix_schema_types_recursive(schema_dict, options, f"tool.{tool.name}.parameters.schema_")
                
        # If aggressive option is enabled, patch all attributes of the declaration
        if options.aggressive:
            if options.verbose and logger.level <= logging.DEBUG:
                logger.debug(f"Aggressive patching for tool: {tool.name}")
            patch_object_attributes(declaration, f"tool.{tool.name}.declaration", options)
        
        return declaration
    
    # Replace the method with our patched version
    tool._get_declaration = patched_get_declaration
    
    # Also patch the tool itself if aggressive is enabled
    if options.aggressive:
        if options.verbose and logger.level <= logging.DEBUG:
            logger.debug(f"Aggressive patching for tool object: {tool.name}")
        patch_object_attributes(tool, f"tool.{tool.name}", options)
    
    return tool


def validate_api_key() -> bool:
    """
    Validate that the OpenAI API key is set.
    
    Returns:
        True if the API key is valid, False otherwise
    """
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        logger.warning("OPENAI_API_KEY environment variable is not set")
        return False
    
    # Basic validation (not empty and minimum length)
    if len(api_key) < 20:  # Typical OpenAI keys are much longer
        logger.warning(f"OPENAI_API_KEY seems too short: {len(api_key)} chars")
        return False
        
    logger.info(f"OpenAI API key found (length: {len(api_key)})")
    return True


def fix_tool_schema_objects(tools):
    """Fix schemas at the object/class level where they might not be exposed.
    
    This function looks for known problematic tools and fixes their schemas
    before they're processed by the standard mechanism.
    
    Args:
        tools: List of tools to fix
        
    Returns:
        Fixed tools list
    """
    for tool in tools:
        if hasattr(tool, 'name'):
            # Fix contractCall specifically
            if tool.name == "contractCall":
                logger.info(f"Checking contractCall tool schema")
                
                # Fix schema directly
                if hasattr(tool, 'schema') and isinstance(tool.schema, dict):
                    schema = tool.schema
                    # Fix args property
                    if 'properties' in schema and 'args' in schema['properties']:
                        args_prop = schema['properties']['args']
                        if args_prop.get('type') == 'array' and 'items' not in args_prop:
                            args_prop['items'] = {'type': 'string'}
                            logger.info("Fixed contractCall schema args array")
                
                # Try alternative schema locations
                for attr in ['_schema', 'function_schema', '_function_schema']:
                    if hasattr(tool, attr) and isinstance(getattr(tool, attr), dict):
                        schema = getattr(tool, attr)
                        if 'properties' in schema and 'args' in schema['properties']:
                            args_prop = schema['properties']['args']
                            if args_prop.get('type') == 'array' and 'items' not in args_prop:
                                args_prop['items'] = {'type': 'string'}
                                logger.info(f"Fixed contractCall {attr} args array")
    
    return tools


async def create_patched_toolset(
    connection_params, 
    options: SchemaFixOptions = None,
    logger_level=logging.INFO
) -> Tuple[List[MCPTool], Any]:
    """
    Create an MCP toolset with patched tools.
    
    Args:
        connection_params: Parameters for connecting to the MCP server
        options: Schema fix options
        logger_level: Logging level for schema fix logs
    
    Returns:
        A tuple of (tools, exit_stack)
    """
    # Only configure logging if it hasn't been configured already
    # This avoids overriding the settings from the main module
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logger_level)
    
    # Set the schema_fix logger level
    logger.setLevel(logger_level)
    
    if options is None:
        options = SchemaFixOptions()
    
    # Validate API key silently unless verbose is enabled
    if options.validate_api_key and (logger_level <= logging.INFO or options.verbose):
        validate_api_key()
    
    try:
        # Get the original toolset and exit_stack
        tools, exit_stack = await MCPToolset.from_server(connection_params=connection_params)
        
        # First fix known schema issues at the object level
        tools = fix_tool_schema_objects(tools)
        
        # Then patch each tool
        fixed_tools = []
        for tool in tools:
            if isinstance(tool, MCPTool):
                if options.verbose and logger_level <= logging.DEBUG:
                    logger.debug(f"Patching tool: {tool.name}")
                fixed_tools.append(patch_mcp_tool(tool, options))
            else:
                fixed_tools.append(tool)
        
        if logger_level <= logging.INFO:
            logger.info(f"Patched {len(fixed_tools)} MCP tools")
        return fixed_tools, exit_stack
    except Exception as e:
        logger.error(f"Error creating patched toolset: {e}")
        raise 