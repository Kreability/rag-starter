import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { cn } from '../lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
        warning: 'border-amber-400/20 bg-amber-400/10 text-amber-400',
        success: 'border-emerald-400/20 bg-emerald-400/10 text-emerald-400',
        outline: 'text-foreground'
      }
    },
    defaultVariants: { variant: 'default' }
  }
)

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
